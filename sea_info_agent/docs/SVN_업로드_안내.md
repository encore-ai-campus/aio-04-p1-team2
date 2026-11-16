# SVN 업로드 안내

`.svnignore`는 SVN 제외 패턴의 원본 파일입니다. SVN은 이 파일을 자동으로 읽지 않으므로 변경 후 프로젝트 루트에서 다음 명령을 실행하세요.

```powershell
svn propset svn:ignore -F .svnignore .
svn propset svn:global-ignores -F .svnignore .
svn status --no-ignore
```

`svn:global-ignores`는 하위 폴더에도 적용됩니다(SVN 1.8 이상). 파일과 함께 루트 폴더의 속성 변경도 커밋하세요. 이미 버전 관리에 추가된 파일에는 ignore가 적용되지 않습니다.

제외 대상: Git 메타데이터, 로컬 환경변수 및 secrets.toml, Python 가상환경과 캐시, 실행 로그, data 폴더(DB와 사용자 업로드), 로컬 IDE 설정.

`.env.example`, `.streamlit/config.toml`, 소스 코드, assets/static, mock 샘플과 SQL, 문서, requirements.txt 및 실행 스크립트는 포함합니다. 새 환경변수 파일을 만들면 비밀값이 있는 파일의 이름을 제외 패턴에 추가하세요.

Git 이력은 삭제하지 않고 `C:\svn\sea_info-git-backup-20261111`에 이동했습니다. 업로드 대상은 `C:\svn\sea_info`입니다.

업로드 전 `.env.example` 등 포함 파일에 실제 비밀값이나 개인정보가 없는지 확인하세요. 이 정리는 비밀값 전체 검사를 대신하지 않습니다.