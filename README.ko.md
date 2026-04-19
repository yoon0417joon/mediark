# mediark

[English README](README.md)

이미지 · GIF · 영상을 **OCR / 태그 / 음성 인식** 4가지 방법으로 검색하는 로컬 미디어 검색 엔진.

- **OCR 검색** — 이미지 속 텍스트 (한국어 포함)
- **WD14 태그 검색** — 애니메이션·일러스트 스타일 태그 (WD EVA-02 Large v3)
- **RAM++ 태그 검색** — 자연 언어 태그 (Recognize Anything Plus)
- **STT 검색** — 영상 음성 텍스트 (Whisper)

FTS5 키워드 필터링 후 벡터 유사도(sentence-transformers)로 정렬하며, 모든 처리는 **로컬**에서 실행된다.

---

## 지원 환경

| OS | Python | OCR 백엔드 |
|----|--------|-----------|
| Ubuntu 22.04+ | 3.10+ | PaddleOCR 2.7.3 |
| macOS (Apple Silicon / Intel) | 3.10+ | EasyOCR |
| Windows 10 / 11 | 3.10+ | PaddleOCR 2.7.3 |

> **ffmpeg** 가 별도로 필요합니다 (영상 처리 · STT 추출).

---

## 빠른 시작

### 1. ffmpeg 설치

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows — https://ffmpeg.org/download.html 에서 설치 후 PATH 추가
```

### 2. 저장소 클론 및 설치

```bash
git clone https://github.com/yoon0417joon/mediark.git
cd mediark
```

**Linux / macOS:**
```bash
bash setup.sh
```

**Windows:**
```bat
setup.bat
```

설치 스크립트가 가상환경 생성 → OS별 의존성 설치 → RAM++ 설치 → `.env` 파일 초기화까지 자동으로 수행한다.

### 3. 갤러리 경로 설정

`.env` 파일을 열어 `GALLERY_ROOT` 를 실제 이미지 폴더 경로로 변경한다:

```dotenv
GALLERY_ROOT=/path/to/your/images
```

### 4. 서버 실행

**Linux / macOS:**
```bash
source .venv/bin/activate
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

**Windows:**
```bat
.venv\Scripts\activate
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

브라우저에서 `http://localhost:8000` 접속.

### 5. 미디어 인덱싱

```bash
python -m server.ingest.pipeline full
```

신규 파일만 처리하며, 완료 후 검색이 가능하다.

---

## 인덱싱 방법

| 방법 | 설명 |
|------|------|
| `pipeline full` | 신규 파일 전체 인덱싱 (OCR + 태깅 + 임베딩) |
| `pipeline ocr` | OCR / 태그 / 썸네일 단계만 실행 |
| `pipeline embed` | 임베딩 + Qdrant 저장 단계만 실행 |
| `POST /ingest` | API로 백그라운드 인덱싱 트리거 |
| Watchdog | `GALLERY_ROOT` 변경 감지 시 자동 인덱싱 |

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/search` | 검색 (`ocr_q`, `wd14_q`, `ram_q`, `stt_q`) |
| `GET` | `/random` | 랜덤 미디어 반환 |
| `GET` | `/media/{id}` | 원본 파일 다운로드 |
| `GET` | `/thumb/{id}` | 썸네일 이미지 |
| `GET` | `/info/{id}` | 미디어 메타데이터 (OCR / 태그) |
| `POST` | `/upload` | 파일 업로드 |
| `POST` | `/ingest` | 인덱싱 파이프라인 트리거 |
| `GET` | `/status` | 인덱싱 진행 상태 |
| `GET` | `/tags/suggest` | 태그 자동완성 |
| `GET` | `/watchdog/status` | 파일 감시 상태 |

---

## 주요 환경변수

전체 목록은 `.env.example` 참고. 자주 쓰는 변수:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `GALLERY_ROOT` | `./images_sample` | 갤러리 루트 경로 |
| `API_KEY` | (없음) | 설정 시 API 키 인증 활성화 |
| `QDRANT_URL` | (없음) | 외부 Qdrant 서버 URL |
| `OCR_BACKEND` | OS 자동 | `paddleocr` 또는 `easyocr` |
| `STT_MODEL` | `base` | Whisper 모델 크기 |

---

## 파일 구조

```
imgsearchengine/
├── server/
│   ├── main.py          # FastAPI 앱
│   ├── config.py        # 전체 설정
│   ├── ingest/          # 파이프라인 (OCR, 태거, STT, 썸네일)
│   ├── search/          # 벡터 검색 + 재순위
│   └── db/              # SQLite + Qdrant 래퍼
├── client/
│   └── index.html       # 웹 UI (단일 파일)
├── tests/
├── .env.example
├── requirements-base.txt
├── requirements-linux.txt
├── requirements-mac.txt
├── requirements-windows.txt
├── setup.sh             # Linux/macOS 설치 스크립트
└── setup.bat            # Windows 설치 스크립트
```

---

## 문제 해결

**Qdrant-SQLite 정합성 불일치:**
```bash
python -c "from server.db.sqlite import init_db; from server.ingest.pipeline import repair_qdrant_consistency; init_db(); repair_qdrant_consistency()"
```

**태그 자동완성 결과 없음:**
```bash
python -c "from server.db.sqlite import init_db, rebuild_tag_stats; init_db(); rebuild_tag_stats()"
```

**RAM++ 태그 누락:**
```bash
python -m server.ingest.repair_ram_tags
```

---

## 로드맵

- [ ] 중복 파일 감지 — SHA-256 완전 동일 파일 업로드 거부; 기존 갤러리 일괄 해시 백필
- [ ] 다중 사용자 인증 — JWT 로그인, viewer / uploader / moderator / admin 역할 계층, 초대 코드 가입
- [ ] 유저별 검색 설정 — 벡터 vs 완전 일치, 태그 부분/완전 일치, AND/OR 키워드 논리
- [ ] 유저 및 서버 관리 — 어드민 패널, 서버 프로필 (공개 범위·허용 포맷), 다중 서버 클라이언트 프리셋
- [ ] 콘텐츠 모더레이션 — 신고 대기열, 숨김/삭제 처리, 권한별 moderator 접근 제어
- [ ] 저장소 용량 관리 — 접근 점수 기반 LRU 자동 삭제; Pin으로 삭제 대상 제외
- [ ] 포맷 확장 — 오디오 (mp3 / wav / flac / m4a), PDF, EPUB 인제스천
- [ ] 플러그인 파이프라인 — 스테이지별 ON/OFF (OCR / WD14 / RAM++ / STT), 인제스천 스케줄링, 선택 재처리
- [ ] 태그 관리 — alias·계층 설정, 개별 미디어 편집, 수치형 범위 슬라이더 검색
- [ ] 역방향 이미지 검색, 유사 중복 감지 (pHash + 벡터), 스마트 컬렉션
- [ ] 확장형 플러그인 생태계 (11개 카테고리) + 관리자 대시보드
- [ ] 테마 시스템, 연합 검색, 대규모 성능 최적화 *(장기)*

---

## 기여

[CONTRIBUTING.md](CONTRIBUTING.md) 참고.

## 라이선스

[MIT License](LICENSE)
