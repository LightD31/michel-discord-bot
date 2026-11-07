"""YouTube: which new uploads a guild is told about, and what the notification says."""

import pytest

from features.youtube import (
    DEFAULT_TEMPLATE,
    classify,
    render_notification,
    short_max_seconds,
    wanted,
)


def item(duration="PT10M", live="none"):
    return {"snippet": {"liveBroadcastContent": live}, "contentDetails": {"duration": duration}}


@pytest.mark.parametrize(
    ("video", "kind"),
    [
        (item("PT10M"), "vod"),
        (item("PT1M30S"), "short"),  # exactly the threshold
        (item("PT1M31S"), "vod"),
        (item("PT45S"), "short"),
        (item("P0D", live="upcoming"), "live"),
        (item("PT0S", live="live"), "live"),
    ],
)
def test_classify(video, kind):
    assert classify(video, 90) == kind


def test_the_short_threshold_comes_from_the_guild():
    assert classify(item("PT2M"), 180) == "short"
    assert short_max_seconds({}) == 90
    assert short_max_seconds({"youtubeShortMaxSeconds": "120"}) == 120
    assert short_max_seconds({"youtubeShortMaxSeconds": 0}) == 1
    assert short_max_seconds({"youtubeShortMaxSeconds": "abc"}) == 90


def test_only_vods_are_wanted_by_default():
    assert wanted("vod", {})
    assert not wanted("short", {})
    assert not wanted("live", {})


def test_each_kind_follows_its_own_toggle():
    cfg = {"shorts": True, "live": True, "vod": False}
    assert wanted("short", cfg) and wanted("live", cfg) and not wanted("vod", cfg)


def test_the_template_is_filled():
    rendered = render_notification(
        "{label} ({handle}) : https://youtu.be/{video_id}",
        video_id="abc",
        handle="alpha",
        label="Alpha",
    )
    assert rendered == "Alpha (alpha) : https://youtu.be/abc"


@pytest.mark.parametrize("template", ["{unknown}", "{0}", "{video_id", "{handle.upper.x}"])
def test_a_broken_template_falls_back_to_the_video_link(template):
    rendered = render_notification(template, video_id="abc", handle="alpha", label="Alpha")
    assert rendered == DEFAULT_TEMPLATE.format(video_id="abc")
