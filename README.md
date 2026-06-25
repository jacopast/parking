# Parking Layout Lab

Rhino `.3dm` 파일을 업로드해 사이트 참고선으로 깔고, 주차면 규격/각도/통로 폭을 조절하며 빠르게 배치안을 스케치하는 웹 프로토타입입니다.

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

- Rhino `.3dm` 파일 업로드
- Rhino 레이어, 오브젝트 수, 모델 경계 확인
- 읽어낸 모델 경계를 설계 캔버스 크기에 반영
- 대지 폭/깊이, 주차면 폭/깊이, 행/열, 통로 폭, 주차 각도 조절
- SVG 기반 주차면 배치 미리보기
- 배치안 JSON/SVG 내보내기

## Notes

Rhino 파일은 브라우저에서 `rhino3dm`으로 읽습니다. 파일 내 geometry 타입에 따라 일부 참고선은 경계 박스로 대체될 수 있습니다.
