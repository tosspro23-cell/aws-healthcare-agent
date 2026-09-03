from care_agent.cli import main


def test_ask_command_prints_answer(capsys):
    exit_code = main(["ask", "What should I focus on first?"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "162" in out


def test_ask_command_with_trace_flag(capsys):
    exit_code = main(["ask", "What should I focus on first?", "--trace"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "--- TRACE ---" in out
    assert '"intent": "priority_focus"' in out


def test_eval_samples_command_runs_all_three(capsys):
    exit_code = main(["eval-samples"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "q_main" in out
    assert "q_missing_context" in out
    assert "q_supplements" in out
