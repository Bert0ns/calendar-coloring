import asyncio
import json
from pathlib import Path

from textual.widgets import Input, Label

from unical.tui.wizard import CredentialsWizardApp, WizardCredentials


def test_credentials_wizard_submits_valid_file(tmp_path: Path) -> None:
    source = tmp_path / "valid.json"
    source.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "id1",
                    "client_secret": "sec1",
                }
            }
        )
    )
    dest = tmp_path / "dest" / "credentials.json"

    app = CredentialsWizardApp(destination=dest)

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, WizardCredentials)
            input_widget = screen.query_one("#credentials-input", Input)
            input_widget.value = str(source)
            await pilot.click("#btn-import")
            await pilot.pause()

    asyncio.run(scenario())
    assert app.return_value == dest.resolve()
    assert dest.exists()


def test_credentials_wizard_enter_key_submits(tmp_path: Path) -> None:
    source = tmp_path / "valid2.json"
    source.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "id2",
                    "client_secret": "sec2",
                }
            }
        )
    )
    dest = tmp_path / "dest" / "credentials2.json"

    app = CredentialsWizardApp(destination=dest)

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = pilot.app.screen
            input_widget = screen.query_one("#credentials-input", Input)
            input_widget.value = str(source)
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert app.return_value == dest.resolve()


def test_credentials_wizard_shows_error_for_empty_input(tmp_path: Path) -> None:
    dest = tmp_path / "dest" / "credentials.json"
    app = CredentialsWizardApp(destination=dest)

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, WizardCredentials)
            error_label = screen.query_one("#credentials-error", Label)

            await pilot.click("#btn-import")
            await pilot.pause()
            assert "Please enter or drag-and-drop" in str(error_label.render())

    asyncio.run(scenario())
    assert app.return_value is None


def test_credentials_wizard_shows_error_for_missing_file(tmp_path: Path) -> None:
    dest = tmp_path / "dest" / "credentials.json"
    app = CredentialsWizardApp(destination=dest)

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, WizardCredentials)
            error_label = screen.query_one("#credentials-error", Label)

            input_widget = screen.query_one("#credentials-input", Input)
            input_widget.value = str(tmp_path / "nope.json")
            await pilot.press("enter")
            await pilot.pause()
            assert "does not exist" in str(error_label.render())

    asyncio.run(scenario())
    assert app.return_value is None


def test_credentials_wizard_shows_error_for_service_account(tmp_path: Path) -> None:
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(json.dumps({"type": "service_account"}))

    dest = tmp_path / "dest" / "credentials.json"
    app = CredentialsWizardApp(destination=dest)

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            screen = pilot.app.screen
            assert isinstance(screen, WizardCredentials)
            error_label = screen.query_one("#credentials-error", Label)

            input_widget = screen.query_one("#credentials-input", Input)
            input_widget.value = str(sa_file)
            await pilot.press("enter")
            await pilot.pause()
            assert "Service Account key" in str(error_label.render())

    asyncio.run(scenario())
    assert app.return_value is None


def test_credentials_wizard_cancel_button(tmp_path: Path) -> None:
    dest = tmp_path / "dest" / "credentials.json"
    app = CredentialsWizardApp(destination=dest)

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#btn-cancel")
            await pilot.pause()

    asyncio.run(scenario())
    assert app.return_value is None


def test_credentials_wizard_escape_cancels(tmp_path: Path) -> None:
    dest = tmp_path / "dest" / "credentials.json"
    app = CredentialsWizardApp(destination=dest)

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(scenario())
    assert app.return_value is None
