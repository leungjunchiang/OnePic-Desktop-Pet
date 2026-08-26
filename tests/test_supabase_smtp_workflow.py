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


def test_auth_config_includes_password_policy_and_recovery_redirect():
    script = (ROOT / "scripts" / "configure_supabase_auth.ps1").read_text(encoding="utf-8")
    config = (ROOT / "config" / "social_backend.json").read_text(encoding="utf-8")

    assert "password_min_length = 8" in script
    assert "mailer_otp_length = 6" in script
    assert "mailer_otp_exp = 600" in script
    assert "mailer_templates_recovery_content" in script
    assert "{{ .Token }}" in script
    assert "some Supabase plans" in script
    assert "uri_allow_list = $resolvedPasswordResetUrl.Trim()" in script
    assert "password_reset_redirect_to" in config


def test_account_security_workflow_can_run_without_smtp_credentials():
    workflow = (ROOT / ".github" / "workflows" / "configure-supabase-account-security.yml").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "configure_supabase_auth.ps1").read_text(encoding="utf-8")

    assert "-SkipSmtp" in workflow
    assert "[switch]$SkipSmtp" in script
    assert "password_min_length = 8" in script


def test_account_security_migration_is_authenticated_and_uid_scoped():
    migration = (ROOT / "supabase" / "migrations" / "20260823000100_lili_account_security.sql").read_text(encoding="utf-8")

    assert "security definer" in migration.lower()
    assert "auth.uid()" in migration
    assert "delete from auth.users" in migration.lower()
    assert "revoke all on function public.lili_delete_my_account() from public, anon, authenticated" in migration
    assert "grant execute on function public.lili_delete_my_account() to authenticated" in migration


def test_password_reset_page_uses_publishable_client_and_update_user():
    page = (ROOT / "docs" / "password-reset.html").read_text(encoding="utf-8")

    assert "sb_publishable_" in page
    assert "createClient" in page
    assert "auth.updateUser({ password })" in page
    assert "service_role" not in page


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


