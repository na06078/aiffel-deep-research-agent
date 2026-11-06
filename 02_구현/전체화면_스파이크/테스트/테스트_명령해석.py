import pytest

from 서버.명령해석 import CommandError, parse_command


def test_웹_명령만보내면_질문없이_자료모드선택():
    result = parse_command("/웹", selected_mode="local")
    assert result["source_mode"] == "web"
    assert result["mode_only"] is True
    assert result["text"] == ""
    assert result["queued"] is False
    assert result["raw_text"] == "/웹"


@pytest.mark.parametrize("prefix", ["/디비", "/DB", "/데이터셋"])
def test_세가지_데이터셋별칭은_웹선택에서_local로_전환(prefix):
    result = parse_command(prefix, selected_mode="web")
    assert result["source_mode"] == "local"
    assert result["mode_only"] is True
    assert result["text"] == ""


def test_접두어와_질문을_함께전달():
    result = parse_command(" /웹 공식 자료를 찾아 줘 ", selected_mode="local")
    assert result == {
        "raw_text": " /웹 공식 자료를 찾아 줘 ",
        "text": "공식 자료를 찾아 줘",
        "source_mode": "web",
        "queued": False,
        "mode_only": False,
    }


@pytest.mark.parametrize("command", ["/큐 /웹 다음 조사", "/웹 /큐 다음 조사"])
def test_큐와자료접두어를함께해석하고_질문만분리(command):
    result = parse_command(command, selected_mode="local")
    assert result["queued"] is True
    assert result["source_mode"] == "web"
    assert result["text"] == "다음 조사"
    assert result["mode_only"] is False


@pytest.mark.parametrize("command", ["/큐", "/큐 /웹"])
def test_질문없는큐는_모드전환으로취급하지않음(command):
    with pytest.raises(CommandError, match="empty_instruction"):
        parse_command(command)


@pytest.mark.parametrize("mode", ["local", "web"])
def test_일반질문은_현재선택모드를유지(mode):
    result = parse_command("다음 질문", selected_mode=mode)
    assert result["source_mode"] == mode
    assert not result["mode_only"] and not result["queued"]


def test_큐의일반질문에도_접수시선택모드를적용():
    result = parse_command("/큐 다음 질문", selected_mode="web")
    assert result["queued"] and result["source_mode"] == "web"
    assert result["text"] == "다음 질문"


def test_명시모드는_일반질문에적용하되_접두어충돌은거절():
    assert parse_command("질문", explicit_mode="web")["source_mode"] == "web"
    assert parse_command("/웹 질문", explicit_mode="web")["text"] == "질문"
    with pytest.raises(CommandError, match="source_mode_conflict"):
        parse_command("/웹 질문", explicit_mode="local")


@pytest.mark.parametrize("command", ["/db 질문", "/web 질문", "/웹검색 질문", "/DBfoo 질문"])
def test_등록하지않은별칭이나부분문자열을_모드명령으로바꾸지않음(command):
    result = parse_command(command, selected_mode="web")
    assert result["source_mode"] == "web"
    assert result["text"] == command
    assert result["mode_only"] is False


@pytest.mark.parametrize("command", ["/웹 /DB 질문", "/웹 /웹 질문", "/큐 /큐 질문"])
def test_중복명령은_임의로하나를선택하지않음(command):
    with pytest.raises(CommandError, match="invalid_command_combination"):
        parse_command(command)


@pytest.mark.parametrize("mode", [None, "", "WEB", True])
def test_잘못저장된선택모드를_데이터셋으로위장하지않음(mode):
    with pytest.raises(CommandError, match="invalid_source_mode"):
        parse_command("질문", selected_mode=mode)


@pytest.mark.parametrize("text,code", [(None, "invalid_instruction"), (" ", "empty_instruction")])
def test_잘못된입력은_안전오류(text, code):
    with pytest.raises(CommandError, match=code):
        parse_command(text)
