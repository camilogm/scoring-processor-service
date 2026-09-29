from app.pipeline.extract import MAX_FRAMES, frame_times

C30_CUTS = [1.42, 2.33, 3.38, 4.5, 5.54, 6.71, 8.25, 11.83, 15.29, 17.0, 19.21, 23.17, 28.75, 30.04, 33.17]


def _gaps(times, duration):
    edges = [0.0, *times, duration]
    return [b - a for a, b in zip(edges, edges[1:])]


def test_frames_cover_the_whole_clip_and_follow_cuts():
    times = frame_times(46.63, C30_CUTS)

    assert times == sorted(times)
    assert times[:3] == [0.2, 1.5, 2.8]  # hook stays dense
    assert len(times) <= MAX_FRAMES
    assert max(_gaps(times, 46.63)) <= 6.0  # no more 17–30 s holes
    assert all(b - a >= 1.0 for a, b in zip(times[3:], times[4:]))
    assert any(0 < t - c <= 0.5 for c in C30_CUTS if 17 <= c <= 31 for t in times)


def test_long_clip_is_capped_and_still_covered():
    cuts = [i * 2.5 for i in range(1, 48)]

    times = frame_times(120.0, cuts)

    assert len(times) == MAX_FRAMES
    assert max(_gaps(times, 120.0)) <= 15.0


def test_short_clip_keeps_only_frames_inside_it():
    assert frame_times(2.5, []) == [0.2, 1.5]


def test_small_frame_budget_spreads_the_grid_before_adding_cut_frames():
    # Small budgets (a 4k-context local model fits about 6 frames) still cover the whole clip.
    times = frame_times(46.63, C30_CUTS, max_frames=8)

    assert len(times) == 8
    assert times[:3] == [0.2, 1.5, 2.8]
    assert max(_gaps(times, 46.63)) <= 11.5
