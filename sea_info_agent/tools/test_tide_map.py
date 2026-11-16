"""공식 지도·지점 API 회귀. 운영 DB나 외부 API를 호출하지 않는다."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from backend.core.tide_stations import station_catalog, stations_by_id
from backend.core.timefmt import KST
from backend.routers import tide
from components import tide_card
from services.api import ApiError

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=KST)


def forecast(code="SO_0553", count=4):
    station = stations_by_id()[code]
    rows = [{"obsvtrNm": station["name"], "lat": station["latitude"], "lot": station["longitude"],
             "predcDt": "2026-11-13 " + hour, "predcTdlvVl": level, "extrSe": kind}
            for hour, kind, level in [("02:00", "2", 20), ("08:00", "1", 90),
                                      ("14:00", "4", 10), ("20:00", "3", 100)]]
    return {"response": {"header": {"resultCode": "00"},
                          "body": {"items": {"item": rows[:count]}, "totalCount": count}}}


class TideTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        settings = SimpleNamespace(tide_api_key="test", observation_api_key="test")
        patches = [patch.object(tide, "get_settings", return_value=settings),
                   patch.object(tide, "now", return_value=NOW),
                   patch.object(tide.redis_store, "cache_get", side_effect=lambda key: deepcopy(self.cache.get(key))),
                   patch.object(tide.redis_store, "cache_set", side_effect=lambda key, data, ttl: self.cache.update({key: deepcopy(data)}))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def response(self, data):
        return Mock(status_code=200, json=lambda: deepcopy(data))

    def test_all_coordinates_verified_and_catalog_never_calls_provider(self):
        with patch.object(tide.httpx, "get") as get:
            data = tide.stations()
        self.assertEqual(data["total"], 166)
        self.assertEqual(len({s["id"] for s in data["stations"]}), 166)
        self.assertEqual(sum(bool(s["observation_id"]) for s in data["stations"]), 53)
        self.assertEqual(len(station_catalog()["observation_stations"]), 55)
        self.assertTrue(all(s["latitude"] and s["longitude"] and s["coordinate_source"] for s in data["stations"]))
        get.assert_not_called()

    def test_http_routes_and_unknown_station(self):
        app = FastAPI()
        app.include_router(tide.router)
        with TestClient(app) as client:
            self.assertEqual(client.get("/tide/stations").json()["total"], 166)
            self.assertEqual(client.get("/tide/stations/not-real").status_code, 404)

    def test_all_events_heights_and_unavailable_observation(self):
        with patch.object(tide.httpx, "get", return_value=self.response(forecast())) as get:
            result = tide.station_tide("SO_0553")
        self.assertEqual(result.station_name, "해운대")
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.high, ["08:00", "20:00"])
        self.assertEqual(result.low, ["02:00", "14:00"])
        self.assertEqual(result.events[0].level_cm, 20)
        self.assertEqual(result.observation_status, "unavailable")
        self.assertEqual(get.call_count, 1)

    def test_valid_cache_reused_and_explicit_refresh_fetches(self):
        with patch.object(tide.httpx, "get", return_value=self.response(forecast())) as get:
            tide.station_tide("SO_0553")
            tide.station_tide("SO_0553")
            self.assertEqual(get.call_count, 1)
            tide.station_tide("SO_0553", refresh=True)
            self.assertEqual(get.call_count, 2)

    def test_failed_forecast_not_cached_and_retry_recovers(self):
        with patch.object(tide.httpx, "get", side_effect=[OSError(), self.response(forecast())]) as get:
            with self.assertRaises(HTTPException):
                tide.station_tide("SO_0553")
            self.assertFalse(self.cache)
            self.assertEqual(len(tide.station_tide("SO_0553", refresh=True).events), 4)
            self.assertEqual(get.call_count, 2)

    def test_cached_other_date_station_and_events_rejected(self):
        with patch.object(tide.httpx, "get", return_value=self.response(forecast())) as get:
            tide.station_tide("SO_0553")
            key = next(iter(self.cache))
            for field, value in [("target_date", "2026-11-12"), ("station_id", "DT_0005"), ("events", [])]:
                self.cache[key][field] = value
                tide.station_tide("SO_0553")
            self.assertEqual(get.call_count, 4)

    def test_incomplete_wrong_coordinate_or_nonfinite_level_rejected(self):
        for mutate in [lambda p: p["response"]["body"].update(totalCount=5),
                       lambda p: p["response"]["body"]["items"]["item"][0].update(lat=34),
                       lambda p: p["response"]["body"]["items"]["item"][0].update(predcTdlvVl=float("nan"))]:
            p = forecast()
            mutate(p)
            with patch.object(tide.httpx, "get", return_value=self.response(p)):
                with self.assertRaises(HTTPException):
                    tide.station_tide("SO_0553")

    def test_forecast_survives_observation_failure(self):
        with patch.object(tide.httpx, "get", side_effect=[self.response(forecast("DT_0005")), OSError()]):
            result = tide.station_tide("DT_0005")
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.observation_status, "error")

    def test_observation_name_coordinate_time_validated_and_separate_cache(self):
        station = stations_by_id()["DT_0005"]
        row = {"obsvtrNm": "부산", "lat": station["latitude"], "lot": station["longitude"],
               "obsrvnDt": "2026-11-13 11:59", "bscTdlvHgt": 30, "wtem": 19}
        payload = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [row]}}}}
        with patch.object(tide.httpx, "get", side_effect=[self.response(forecast("DT_0005")), self.response(payload)]) as get:
            one = tide.station_tide("DT_0005")
            two = tide.station_tide("DT_0005")
            self.assertEqual(get.call_count, 2)
        self.assertEqual(one.observation_status, "ok")
        self.assertEqual(two.observation_water_temp_c, 19)
        self.assertTrue(two.observation_collected_at)
        for field, value in [("obsvtrNm", "해운대"), ("lat", 34), ("obsrvnDt", "2026-11-12 11:59")]:
            bad = deepcopy(payload)
            bad["response"]["body"]["items"]["item"][0][field] = value
            with patch.object(tide.httpx, "get", return_value=self.response(bad)):
                self.assertEqual(tide._fetch_observation("DT_0005")["observation_status"], "error")

    def test_kst_rollover_uses_new_forecast_cache(self):
        with patch.object(tide.httpx, "get", return_value=self.response(forecast())):
            tide.station_tide("SO_0553")
        payload = forecast()
        for row in payload["response"]["body"]["items"]["item"]:
            row["predcDt"] = row["predcDt"].replace("2026-11-13", "2026-11-14")
        with patch.object(tide, "now", return_value=NOW + timedelta(days=1)), patch.object(tide.httpx, "get", return_value=self.response(payload)) as get:
            self.assertEqual(tide.station_tide("SO_0553").target_date, "2026-11-14")
            get.assert_called_once()


class TideUITests(unittest.TestCase):
    def setUp(self):
        self.get = Mock()
        for p in [patch("services.db.tide_stations", return_value=tide.stations()),
                  patch("services.db.station_tide", self.get), patch("services.auth.current_user", return_value=None),
                  patch("components.tide_card.tide_map", return_value=None)]:
            p.start()
            self.addCleanup(p.stop)

    def app(self):
        return AppTest.from_string("from components.tide_card import tide_card\ntide_card()", default_timeout=15)

    def test_home_does_not_fetch_details(self):
        at = self.app().run()
        self.assertFalse(at.exception)
        self.get.assert_not_called()
        self.assertEqual(len(at.selectbox[0].options), 166)

    def test_list_selection_failure_keeps_name_and_retry(self):
        self.get.side_effect = ApiError(503, "시험 예보 실패")
        at = self.app().run()
        at.selectbox[0].select("SO_0553")
        at.button[0].click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("해운대" in m.value for m in at.markdown))
        self.assertTrue(any(b.key == "tide_detail_refresh" for b in at.button))
        at.button(key="tide_detail_refresh").click().run()
        self.assertTrue(self.get.call_args.kwargs["refresh"])

    def test_load_rejects_wrong_station_and_date(self):
        for data in [{"station_id": "DT_0005", "target_date": tide_card._today()},
                     {"station_id": "SO_0553", "target_date": "2000-01-01"}]:
            self.get.return_value = data
            self.assertIn("error", tide_card._load("SO_0553", "request", False, False))


if __name__ == "__main__":
    unittest.main()
