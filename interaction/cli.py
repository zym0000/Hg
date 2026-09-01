import asyncio
import time
from typing import Any
from prompt_toolkit.key_binding import KeyBindings
from interaction.slash_commands import render_help as _render_help

HELP_TEXT = _render_help()

def _to_agent_message(text: str) -> Any:
    from agent.message import TextContent, UserMessage

    return UserMessage(
        role="user",
        content=[TextContent(type="text", text=text)],
        timestamp=time.time(),
    )

def _preview(text: str) -> str:
    return (text[:60] + "...") if len(text) > 60 else text

def _now() -> float:
    import time as _time

    return _time.time()

class InteractiveCLI:
    def __init__(
        self,
        agent_session,
        workspace: str,
        session_repo=None,
    ):
        self.agent_session = agent_session
        self.workspace = workspace
        self.session_repo = session_repo
        self.alive = True
        self._prompt = None  # lazy; requires a real terminal

        self._last_tree_ids: list[str] = []
        self._tree_filter: str = "default"

        self._streaming: bool = False

        self._submit_as_follow_up: bool = False

        self._pending_follow_ups: list[str] = []

        self._last_ctrl_c_time: float = 0.0

    def _ensure_prompt(self):
        if self._prompt is None:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import InMemoryHistory

            self._prompt = PromptSession(
                history=InMemoryHistory(),
                key_bindings=self._build_keybindings(),

                prompt_continuation=lambda width, line_number, is_soft_wrap: "... ",
            )
        return self._prompt

    def _build_keybindings(self) -> KeyBindings:

        kb = KeyBindings()

        @kb.add("enter")
        def _(event):
            buf = event.app.current_buffer
            text = buf.text
            if text.endswith("\\") and not text.endswith("\\\\"):
                buf.text = text[:-1]
                buf.cursor_position = len(text) - 1
                buf.insert_text("\n", move_cursor=True)
                return
            # Normal submit.
            buf.validate_and_handle()

        @kb.add("c-j")
        def _(event):
            buf = event.app.current_buffer
            buf.insert_text("\n", move_cursor=True)

        @kb.add("escape", "enter")
        def _(event):
            self._submit_as_follow_up = True
            event.app.current_buffer.validate_and_handle()

        @kb.add("escape")
        def _(event):
            if self._streaming:
                event.app.create_background_task(self._do_cancel_current_run())

        @kb.add("c-c")
        def _(event):
            if self._streaming:
                return
            now = _now()
            if now - self._last_ctrl_c_time < 0.5:
                self.alive = False
                event.app.exit(exception=KeyboardInterrupt)
            else:
                self._last_ctrl_c_time = now
                event.app.current_buffer.text = ""

        @kb.add("c-d")
        def _(event):
            if self._streaming:
                return
            buf = event.app.current_buffer
            if buf.text == "":
                self.alive = False
                event.app.exit(exception=EOFError)
        return kb

    async def _do_cancel_current_run(self) -> None:
        cancelled = await self.agent_session.cancel_current_run()
        if cancelled:
            print("(cancelled)")
        else:
            print("(no active run to cancel)")

    async def _refresh_footer(self, leaf_override: str | None = None) -> None:
        from interaction.display import get_footer

        footer = get_footer()
        # Model id
        try:
            model = getattr(self.agent_session.agent.state, "model", None)
            model_id = getattr(model, "id", None) if model is not None else None
            if model_id is None and model is not None:
                model_id = str(model)
            if model_id:
                footer.update_model(model_id)
        except Exception:
            pass
        # Lane + leaf + breadcrumb
        try:
            active_lane = await self.agent_session.get_active_lane()
            leaf = leaf_override
            if leaf is None:
                try:
                    leaf = await self.agent_session.get_current_leaf()
                except Exception:
                    leaf = None
            breadcrumb = active_lane
            if leaf:
                breadcrumb = f"{active_lane} > {leaf[:8]}"
            footer.update_branch(
                lane=active_lane,
                leaf_id=leaf,
                breadcrumb=breadcrumb,
            )
        except Exception:
            pass

    async def _cmd(self, line: str) -> None:
        if not line:
            return
        if line.startswith("/"):
            from interaction.slash_commands import COMMANDS

            parts = line.split(maxsplit=1)
            cmd_name = parts[0].lower().lstrip("/")
            arg = parts[1] if len(parts) > 1 else ""
            cmd = COMMANDS.get(cmd_name)
            if cmd is None or cmd.handler is None:
                print(f"Unknown command: /{cmd_name}. Try /help.")
                return
            await cmd.handler(self, arg)
            return

        from interaction.display import render_pi_event

        async for event in self.agent_session.run(line):
            render_pi_event(event)

    async def _start_run(self, text: str):
        from interaction.display import render_pi_event

        self._streaming = True
        try:
            gen = self.agent_session.run(text)
            first_event = await gen.__anext__()
            render_pi_event(first_event)
            return gen
        except StopAsyncIteration:
            self._streaming = False
            return None
        except Exception as e:
            print(f"(run error: {e})")
            self._streaming = False
            return None

    async def _drain_run_and_followups(self, gen) -> None:
        from interaction.display import render_pi_event

        try:
            async for event in gen:
                render_pi_event(event)
        except Exception as e:
            print(f"(run error: {e})")
        finally:
            self._streaming = False
            while self._pending_follow_ups and self.alive:
                next_text = self._pending_follow_ups.pop(0)
                followup_gen = await self._start_run(next_text)
                if followup_gen is None:
                    continue
                try:
                    async for event in followup_gen:
                        render_pi_event(event)
                except Exception as e:
                    print(f"(follow-up error: {e})")

    async def _run_and_render(self, text: str) -> None:
        gen = await self._start_run(text)
        if gen is None:
            return
        await self._drain_run_and_followups(gen)

    async def run(self) -> None:
        from interaction.display import print_footer
        from interaction.slash_commands import render_help

        print(render_help())
        try:
            await self._refresh_footer()
            print_footer()
        except Exception:
            pass

        while self.alive:
            try:
                text = await self._ensure_prompt().prompt_async("> ")
            except (EOFError, KeyboardInterrupt):
                self.alive = False
                break
            except asyncio.CancelledError:
                self.alive = False
                raise
            except Exception as e:
                print(f"(cli error: {e})")
                continue

            text = (text or "").strip()
            if not text:
                continue

            was_follow_up = self._submit_as_follow_up
            self._submit_as_follow_up = False

            if text.startswith("/"):
                try:
                    await self._cmd(text)
                except Exception as e:
                    print(f"(command error: {e})")
                continue

            streaming = False
            try:
                streaming = await self.agent_session.is_streaming()
            except Exception:
                streaming = False

            if streaming:
                msg = _to_agent_message(text)
                if was_follow_up:
                    self.agent_session.agent.follow_up(msg)
                    self._pending_follow_ups.append(text)
                    print(f"(queued follow-up: {_preview(text)})")
                else:
                    self.agent_session.agent.steer(msg)
                    print(f"(steering: {_preview(text)})")
                continue

            gen = await self._start_run(text)
            if gen is not None:
                asyncio.create_task(self._drain_run_and_followups(gen))
