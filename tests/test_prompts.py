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
