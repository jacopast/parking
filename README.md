# Parking Layout Lab

Rhino 파일 안에서 바로 주차장 배치안을 빠르게 그려보는 도구입니다. 우선 `rhino/parking_layout.py`를 Rhino Python으로 실행하는 방식이 가장 빠른 워크플로우이고, 브라우저에서 `.3dm` 파일을 참고선으로 불러보는 React 프로토타입도 포함되어 있습니다.

## Rhino Python workflow

1. Rhino에서 대상 `.3dm` 파일을 엽니다.
2. 사이트 외곽선이 될 닫힌 curve/polyline을 준비합니다.
3. Rhino 명령창에서 `RunPythonScript`를 실행합니다.
4. `rhino/parking_layout.py` 파일을 선택합니다.
5. 외곽선을 선택하고 주차면 폭/깊이, 통로 폭, 각도, 여유 폭, 최대 행 수를 입력합니다.

스크립트는 현재 Rhino 파일 안에 다음 레이어를 만들고 geometry를 추가합니다.

- `Parking Layout::Reference Boundary`
- `Parking Layout::Stalls`
- `Parking Layout::Aisles`
- `Parking Layout::Labels`

주차면은 선택한 외곽선의 bounding box 안에 배치되며, 닫힌 외곽선을 선택한 경우 각 주차면 꼭짓점이 외곽선 안에 들어가는 것만 생성합니다.

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

- Rhino 안에서 Python 스크립트로 주차면/통로/번호 레이어 생성
- Rhino `.3dm` 파일 업로드
- Rhino 레이어, 오브젝트 수, 모델 경계 확인
- 읽어낸 모델 경계를 설계 캔버스 크기에 반영
- 대지 폭/깊이, 주차면 폭/깊이, 행/열, 통로 폭, 주차 각도 조절
- SVG 기반 주차면 배치 미리보기
- 배치안 JSON/SVG 내보내기

## Notes

Rhino 파일은 브라우저에서 `rhino3dm`으로 읽습니다. 파일 내 geometry 타입에 따라 일부 참고선은 경계 박스로 대체될 수 있습니다.
