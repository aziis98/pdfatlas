import multiprocessing
import signal
import sys
from unittest.mock import patch

from pdfatlas.core.process_utils import init_child_process_prelude


def test_init_child_process_prelude_parent_noop(monkeypatch):
    proc = multiprocessing.current_process()
    if hasattr(proc, "_inheriting"):
        monkeypatch.delattr(proc, "_inheriting")

    old_stderr = sys.stderr
    old_stdout = sys.stdout

    init_child_process_prelude()

    assert sys.stderr is old_stderr
    assert sys.stdout is old_stdout


def test_init_child_process_prelude_child(tmp_path, monkeypatch):
    log_file = tmp_path / "child.log"
    monkeypatch.setenv("PDFATLAS_CHILD_STDERR_LOG", str(log_file))

    proc = multiprocessing.current_process()
    monkeypatch.setattr(proc, "_inheriting", True, raising=False)

    old_stderr = sys.stderr
    old_stdout = sys.stdout

    with patch("signal.signal") as mock_signal:
        try:
            init_child_process_prelude()

            mock_signal.assert_called_once_with(signal.SIGINT, signal.SIG_IGN)
            assert sys.stderr is not old_stderr
            assert sys.stdout is not old_stdout
            assert log_file.exists()
            content = log_file.read_text()
            assert "render child pid=" in content
        finally:
            # Restore original streams
            if sys.stderr is not old_stderr:
                sys.stderr.close()
                sys.stderr = old_stderr
            if sys.stdout is not old_stdout:
                sys.stdout = old_stdout


def test_launch_pdfatlas_process():
    from unittest.mock import patch
    from pdfatlas.core.process_utils import launch_pdfatlas_process

    with patch("subprocess.Popen") as mock_popen:
        launch_pdfatlas_process("arxiv:2305.12345")
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "arxiv:2305.12345" in cmd
        assert mock_popen.call_args[1].get("close_fds") is True
        assert mock_popen.call_args[1].get("start_new_session") is True

