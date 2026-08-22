from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_supabase_smtp_workflow_is_manual_and_uses_actions_secrets():
    workflow = (ROOT / ".github" / "workflows" / "configure-supabase-auth.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}" in workflow
    assert "SUPABASE_SMTP_PASSWORD: ${{ secrets.SUPABASE_SMTP_PASSWORD }}" in workflow
    assert "./scripts/configure_supabase_auth.ps1" in workflow


def test_supabase_smtp_script_does_not_print_credentials():
    script = (ROOT / "scripts" / "configure_supabase_auth.ps1").read_text(encoding="utf-8")

    assert "smtp_pass = $smtpPassword" in script
    assert "Write-Host $smtpPassword" not in script
    assert "Write-Output $smtpPassword" not in script
    assert "Write-Host $body" not in script

