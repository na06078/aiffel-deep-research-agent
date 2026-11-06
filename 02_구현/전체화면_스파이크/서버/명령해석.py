"""연구 지시와 자료 모드 명령의 순수 해석. 저장·전송을 하지 않는다."""

from 서버.웹계약 import SOURCE_PREFIXES, ParsedCommand


class CommandError(ValueError):
    pass


def parse_command(text, *, selected_mode="local", explicit_mode=None) -> ParsedCommand:
    """자료 접두어와 /큐를 해석한다. 상태 변경은 실행 감독자의 책임이다."""
    if not isinstance(text, str):
        raise CommandError("invalid_instruction")
    if selected_mode not in ("local", "web") or (
        explicit_mode is not None and explicit_mode not in ("local", "web")
    ):
        raise CommandError("invalid_source_mode")
    content = text.strip()
    if not content:
        raise CommandError("empty_instruction")
    queued = False
    requested_mode = None
    while content:
        parts = content.split(maxsplit=1)
        token = parts[0]
        if token == "/큐":
            if queued:
                raise CommandError("invalid_command_combination")
            queued = True
        elif token in SOURCE_PREFIXES:
            if requested_mode is not None:
                raise CommandError("invalid_command_combination")
            requested_mode = SOURCE_PREFIXES[token]
        else:
            break
        content = parts[1].strip() if len(parts) == 2 else ""
    if requested_mode is not None and explicit_mode is not None and requested_mode != explicit_mode:
        raise CommandError("source_mode_conflict")
    mode = requested_mode or explicit_mode or selected_mode
    mode_only = requested_mode is not None and not content and not queued
    if not content and not mode_only:
        raise CommandError("empty_instruction")
    return {
        "raw_text": text,
        "text": content,
        "queued": queued,
        "source_mode": mode,
        "mode_only": mode_only,
    }
