# Merge Template 작업 흐름 — 1차 UI 통합

## 사용 순서

1. Object Mode에서 변형할 Mesh 하나를 선택한다. 이 활성 객체가 Source다.
2. EZ 사이드바의 Merge Template에서 Source Type의 Sphere / Box / Cylinder 중 하나를 누른다.
3. Target 필드의 Blender 기본 스포이드 또는 객체 목록으로 상대 Mesh를 지정한다. 두 객체를 함께 선택할 필요가 없다.
4. Target Type을 Sphere / Box / Cylinder 중에서 고른다.
5. Merge (Preview)를 실행한다. 생성된 기존 Bead 파라미터로 조절한다.

타입 버튼은 사용자 지정 템플릿 선언이다. 객체 이름이나 토폴로지를 자동으로 인식하거나 실제 Mesh를 Sphere 등으로 변환하는 기능이 아니다. 원본이 이미 변형됐어도 사용자가 적절한 타입을 직접 선택한다.

## 이번에 구현한 것

기존 Merge Selected 버튼 대신 단일 Source 기준 UI를 표시한다. Target 포인터와 양쪽 타입 선택은 각 Source Object에 저장되고 .blend에 유지된다. 상대를 바꾸면 이전 상대의 타입 선택은 초기화한다. 기존 Join Selected 및 구형 merge_shapes 연산자는 호환성을 위해 남겨 두었다.

9개 타입 조합의 등록표와 실행 분기 기반을 추가했다. 하지만 현재 모든 조합의 backend는 명확히 표시한 BEAD_PREVIEW이며, 기존 Bead 기본값을 그대로 사용한다. 조합별 새 수치/곡선/최적화가 검증됐다고 주장하지 않는다. 9개 조합 테스트는 선택/디스패치/메타데이터 테스트이고 9개 형상 접합의 품질 인증이 아니다.

Sphere–Box의 Ring 재구성, Sphere–Sphere의 단면 재측정, Cylinder–Cylinder의 새로운 접합부 교체는 아직 이 실행 버튼에 연결하지 않았다. UI에도 그 제한을 표시한다. 향후 검증된 프리셋별 backend를 등록표와 실행 경로에 하나씩 추가한다.

생성한 Bead의 자체 노드 그룹에 원래 템플릿 쌍과 backend를 저장한다. Blender 5.1에서는 임의 모디파이어 ID 프로퍼티가 저장 후 남지 않는 사례가 있어, 저장/재로드 검사를 통과한 노드 그룹 메타데이터를 사용했다. 기존 Merge의 타입/버전 계약은 바꾸지 않았다.

## 안전 및 제한

Self Target, 현재 Scene 밖 대상, 미선택 타입, 이미 같은 상대에 걸린 Merge, 알려진 Merge 참조 체인의 순환을 차단한다. 실행 시 평가된 면이 없는 입력도 거부한다. 실패 도중 새로 만든 모디파이어는 되돌리며 기존 사용자 모디파이어는 남긴다. 소스/타깃 데이터에 Apply/Boolean/Remesh를 직접 적용하지 않는다.

순환 검사는 ezMerge의 알려진 Merge 체인을 대상으로 한다. 외부 애드온/모든 임의 노드 그룹의 전체 의존성 그래프를 분석하는 기능은 아니다. 기존 Bead의 형상 한계는 그대로이며, Preview가 실제 공유 버텍스 접합 또는 Ring Recapture라는 뜻이 아니다.

설정 버튼과 실행은 Blender Undo를 지원한다. 실행에 필요한 정보가 없으면 버튼을 비활성화하고 이유를 표시한다. 실제 스포이드의 마우스 조작은 Blender 기본 Object PointerProperty UI에 맡긴다.

## 검증

Steam Blender 5.1.2 백그라운드에서 실제 개발 폴더를 import해 검사했다.

- Mesh 하나만 선택한 상태에서 6개 타입 버튼과 Target 포인터 UI 구성.
- 9개 타입 조합이 올바른 registry ID/Bead backend로 분기함.
- 실제 Bead 생성, Source 선택 유지, Target 미수정, Source 메시 좌표 보존.
- 중복 및 역방향 Merge 체인 차단.
- 주입한 부분 생성 실패의 모디파이어 rollback.
- 소스별 설정 분리 및 .blend 재로드에서 Target/타입/생성 backend 유지.
- 전체 애드온 register/unregister 반복 및 이전 Recapture/merge_shapes 등록 유지.

스크립트: C:/Users/kikyou/Documents/Codex/2026-10-12/new-chat/merge-template-workflow/test_templates.py

완료 표시: MERGE TEMPLATE WORKFLOW INTEGRATION TEST PASSED.

## 설치본과 개발본을 구분

이번 수정 대상은 C:/Users/kikyou/Documents/Unity Projects/ezMerge다. 현재 Blender 5.1 extensions/user_default/ezhardsurface는 junction이 아닌 별도 설치본으로 확인됐고, __init__.py는 0.30.0 및 weld_classic/weld_gn 모듈을 포함한다. 이 개발 폴더는 0.28.1 계열이다. 서로 다른 설치본을 오래된 코드로 덮어쓰지 않도록 설치 폴더는 변경하지 않았다. 따라서 현재 열려 있는 Blender 설치본에 새 UI가 자동 반영되는 것은 아니다. 설치본 동기화는 별도 확인 후 진행해야 한다.
