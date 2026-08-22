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


def test_supabase_smtp_script_can_derive_public_defaults():
    script = (ROOT / "scripts" / "configure_supabase_auth.ps1").read_text(encoding="utf-8")

    assert "config/social_backend.json" in script
    assert "publicConfig.supabase_url" in script
    assert "publicConfig.email_redirect_to" in script
    assert '"587"' in script


def test_focus_history_deployment_uses_existing_management_token_and_public_project_ref():
    workflow = (ROOT / ".github" / "workflows" / "deploy-supabase-focus-history.yml").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "apply_supabase_focus_history.ps1").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}" in workflow
    assert "vars.SUPABASE_PROJECT_REF" in workflow
    assert "./scripts/apply_supabase_focus_history.ps1" in workflow
    assert "/database/query" in script
    assert "config/social_backend.json" in script
    assert "database_write" in script
    assert "20260822000400_lili_focus_daily_visibility.sql" in script

