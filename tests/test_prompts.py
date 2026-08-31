from ai_film.prompts import build_image_prompt, build_video_prompt


def test_build_image_prompt_combines_action_and_visual():
    shot = {
        "action": "girl steps out of darkness",
        "visual": {"style": "cinematic sci-fi", "lighting": "blue rim light"},
        "camera": {"shot": "close_up"},
    }
    prompt = build_image_prompt(shot)
    assert "girl steps out of darkness" in prompt
    assert "cinematic sci-fi" in prompt
    assert "close_up shot" in prompt


def test_build_video_prompt_adds_camera_movement():
    shot = {"action": "girl steps out of darkness", "visual": {}, "camera": {"movement": "slow_push_in"}}
    prompt = build_video_prompt(shot)
    assert "slow_push_in" in prompt


def test_build_video_prompt_includes_dialogue_when_speaker_is_on_screen():
    shot = {
        "action": "she listens", "visual": {}, "camera": {},
        "dialogue": {"text": "I know.", "speaker": "Mara Voss"},
        "characters": [{"name": "Mara Voss", "reference": "assets/characters/Mara Voss/reference.png"}],
    }
    prompt = build_video_prompt(shot)
    assert "Mara Voss says" in prompt
    assert "I know." in prompt


def test_build_video_prompt_omits_dialogue_when_speaker_is_off_screen():
    """A phone call / narration case: the speaker isn't in the shot's own
    characters[] (someone else is on screen, listening). Injecting the
    text here would hand the model concrete words to wrongly lip-sync the
    on-screen listener to — worse than leaving it out."""
    shot = {
        "action": "she listens to the phone", "visual": {}, "camera": {},
        "dialogue": {"text": "Honey, I think they brought back the wrong man.", "speaker": "Eli Voss"},
        "characters": [{"name": "Mara Voss", "reference": "assets/characters/Mara Voss/reference.png"}],
    }
    prompt = build_video_prompt(shot)
    assert "Eli Voss says" not in prompt
    assert "brought back the wrong man" not in prompt


def test_build_video_prompt_omits_dialogue_when_shot_has_no_characters():
    shot = {
        "action": "an empty corridor", "visual": {}, "camera": {},
        "dialogue": {"text": "hello?", "speaker": "Narrator"},
    }
    prompt = build_video_prompt(shot)
    assert "hello?" not in prompt


def test_build_video_prompt_unaffected_by_empty_dialogue():
    shot = {
        "action": "she walks", "visual": {}, "camera": {},
        "dialogue": {"text": "", "speaker": ""},
        "characters": [{"name": "Mara Voss", "reference": "assets/characters/Mara Voss/reference.png"}],
    }
    prompt = build_video_prompt(shot)
    assert "says" not in prompt
