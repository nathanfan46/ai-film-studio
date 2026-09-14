from ai_film.comfyui.roles import infer_role


def test_exact_structural_types():
    assert infer_role("Reroute") == "structural"
    assert infer_role("Note") == "comment"


def test_substring_match_not_prefix_match():
    # DownloadAndLoadLivePortraitModels contains "LivePortrait" but doesn't
    # start with it -- prefix matching would miss this, substring must not.
    assert infer_role("DownloadAndLoadLivePortraitModels") == "facial_performance_transfer"
    assert infer_role("LivePortraitCropper") == "facial_performance_transfer"
    assert infer_role("DownloadAndLoadMimicMotionModel") == "body_motion_transfer"
    assert infer_role("MimicMotionSampler") == "body_motion_transfer"


def test_other_family_matches():
    assert infer_role("ADE_LoadAnimateDiffModel") == "temporal_consistency"
    assert infer_role("ReActorFaceSwap") == "identity_stabilization"
    assert infer_role("RIFE VFI") == "frame_interpolation"
    assert infer_role("GroundingDinoSAMSegment (segment anything)") == "segmentation"
    assert infer_role("ControlNetLoader") == "control_guidance"
    assert infer_role("VHS_LoadVideo") == "video_io"
    assert infer_role("VHS_VideoCombine") == "video_io"


def test_unmatched_type_returns_none():
    assert infer_role("TotallyUnknownCustomNode") is None


def test_known_layer_b_types_get_a_role_too():
    # Layer A also covers the small Layer B set, per the spec's "exact match
    # against the known-node registry, these nodes get a precise role for
    # free" -- checked once Layer B exists (Task 4); until then these three
    # core types get roles from the substring table's own entries, which is
    # fine since the substring table is a strict superset check here.
    assert infer_role("CLIPTextEncode") == "conditioning"
    assert infer_role("CheckpointLoaderSimple") == "model_load"
    assert infer_role("KSampler") == "sampler"
