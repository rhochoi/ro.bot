"""테스트 공통 설정: AI 도구 연결(ro.engines)은 실제 ~/.claude·~/.codex·~/.gemini·~/.ro 대신 빈 임시 홈을 본다.
펫 서버를 띄우는 테스트도 시작할 때 연결 상태를 묻기 때문에 패키지 단위로 정한다."""
import atexit
import shutil
import tempfile

from ro import engines

engines.HOME = tempfile.mkdtemp(prefix="ro-test-home-")
atexit.register(shutil.rmtree, engines.HOME, True)
