# Parking Layout Lab

Rhino 파일 안에서 바로 주차장 배치안을 빠르게 그려보는 도구입니다. 우선 `rhino/parking_layout.py`를 Rhino Python으로 실행하는 방식이 가장 빠른 워크플로우이고, 브라우저에서 `.3dm` 파일을 참고선으로 불러보는 React 프로토타입도 포함되어 있습니다.

## Rhino Python workflow

1. Rhino에서 대상 `.3dm` 파일을 엽니다.
2. 주차가 가능한 영역을 닫힌 curve/polyline으로 준비합니다.
3. 입구 중심점과 출구 중심점을 클릭할 수 있게 위치를 정합니다.
4. Rhino 명령창에서 `RunPythonScript`를 실행합니다.
5. `rhino/parking_layout.py` 파일을 선택합니다.
6. 가능한 영역 curve, 입구점, 출구점을 순서대로 지정합니다.
7. 주차면 폭/깊이, 통로 폭, 입구-출구 동선 폭, 각도, 여유 폭, 최대 행 수를 입력합니다.

스크립트는 현재 Rhino 파일 안에 다음 레이어를 만들고 geometry를 추가합니다.

- `Parking Layout::Available Area`
- `Parking Layout::Stalls`
- `Parking Layout::Aisles`
- `Parking Layout::Circulation`
- `Parking Layout::Labels`

입구와 출구를 잇는 선을 기준 축으로 잡아 주차열을 정렬하고, 그 사이의 `Circulation` 폭은 비워둡니다. 주차면은 가능한 영역 안에 들어가면서 입구-출구 동선과 겹치지 않는 것만 생성합니다.

## Browser prototype

## Run

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
```

## Features

- Rhino 안에서 Python 스크립트로 가능한 영역, 입구, 출구 기반 자동 주차 레이아웃 생성
- Rhino `.3dm` 파일 업로드
- Rhino 레이어, 오브젝트 수, 모델 경계 확인
- 읽어낸 모델 경계를 설계 캔버스 크기에 반영
- 대지 폭/깊이, 주차면 폭/깊이, 행/열, 통로 폭, 주차 각도 조절
- SVG 기반 주차면 배치 미리보기
- 배치안 JSON/SVG 내보내기

## Notes

Rhino 파일은 브라우저에서 `rhino3dm`으로 읽습니다. 파일 내 geometry 타입에 따라 일부 참고선은 경계 박스로 대체될 수 있습니다.
