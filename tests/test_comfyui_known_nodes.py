from ai_film.comfyui.known_nodes import KNOWN_NODE_REGISTRY


def test_registry_covers_the_v1_type_list():
    expected_types = {
        "CheckpointLoaderSimple", "CLIPTextEncode", "KSampler", "KSamplerAdvanced",
        "VAEEncode", "VAEDecode", "EmptyLatentImage", "LoadImage", "SaveImage",
        "LoraLoader", "LoraLoaderModelOnly",
    }
    assert expected_types.issubset(KNOWN_NODE_REGISTRY.keys())


def test_ksampler_widget_order_matches_real_comfyui_layout():
    entry = KNOWN_NODE_REGISTRY["KSampler"]
    assert entry["addressing"] == "array"
    assert entry["widgets"] == [
        "seed", "control_after_generate", "steps", "cfg",
        "sampler_name", "scheduler", "denoise",
    ]


def test_cliptextencode_text_is_first_widget():
    entry = KNOWN_NODE_REGISTRY["CLIPTextEncode"]
    assert entry["addressing"] == "array"
    assert entry["widgets"][0] == "text"


def test_checkpointloader_ckpt_name_is_first_widget():
    entry = KNOWN_NODE_REGISTRY["CheckpointLoaderSimple"]
    assert entry["widgets"][0] == "ckpt_name"
