# Contributing to Gallery Search System

## 이슈 제보

버그 또는 기능 요청은 GitHub Issues에 다음 정보를 포함해 제보해주세요:

- OS 및 Python 버전
- 재현 단계 (최소한의 예시)
- 예상 동작 vs 실제 동작
- 로그 출력 (있다면)

## 개발 환경 세팅

```bash
git clone https://github.com/your-org/imgsearchengine.git
cd imgsearchengine
bash setup.sh          # Linux/macOS
# 또는 setup.bat       # Windows
```

테스트 실행:
```bash
source .venv/bin/activate
pip install pytest pytest-cov
pytest tests/ -v
```

## PR 가이드

1. `main` 브랜치에서 feature 브랜치 생성: `git checkout -b feat/my-feature`
2. 변경 사항 구현 (코드 스타일: PEP 8, 타입 힌트 필수)
3. 테스트 추가 또는 기존 테스트 통과 확인
4. `ruff check server/` 린트 통과 확인
5. PR 제출 — 변경 이유와 테스트 방법 명시

## 코드 규칙

- 모든 함수에 타입 힌트 사용
- `print()` 대신 `logging.getLogger(__name__)` 사용
- 하드코딩된 경로/상수는 `server/config.py` 경유
- 파일 500줄 상한 (분리 권장)
- 환경변수는 `.env.example` 에 문서화

## 라이선스

기여한 코드는 [MIT License](LICENSE) 하에 공개됩니다.
