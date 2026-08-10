import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# tools/ 는 배포 패키지가 아니라 레포 도구다. import 경로만 열어준다.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
