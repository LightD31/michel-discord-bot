"""Prometheus families for the process health recorded in :mod:`src.core.health`.

Read at scrape time on ``prometheus_client``'s thread:
:meth:`~src.core.health.HealthRegistry.snapshot` copies under a lock, so a
scrape never sees a half-updated record. Counters are cumulative since the
process started; Prometheus' ``rate()``/``increase()`` handle the restarts.

Useful alerts, for instance::

    # A task that hasn't succeeded for 3 of its periods (here: a 5-minute task)
    time() - michel_task_last_success_timestamp_seconds{task="YoutubeExtension.check_youtube"} > 900
    # No backup for over a day
    time() - michel_backup_last_success_timestamp_seconds > 26 * 3600
    # An API failing
    sum by (host) (rate(michel_http_requests_total{outcome=~"5xx|error"}[15m])) > 0.1
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from prometheus_client.metrics_core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from src.core.health import HealthSnapshot


def health_families(snapshot: HealthSnapshot) -> list[Metric]:
    runs = CounterMetricFamily(
        "michel_task_runs",
        "Periodic task runs, per outcome (skipped: the previous run hadn't finished).",
        labels=("task", "status"),
    )
    last_success = GaugeMetricFamily(
        "michel_task_last_success_timestamp_seconds",
        "When each periodic task last ran without raising.",
        labels=("task",),
    )
    last_failure = GaugeMetricFamily(
        "michel_task_last_failure_timestamp_seconds",
        "When each periodic task last raised.",
        labels=("task",),
    )
    duration = GaugeMetricFamily(
        "michel_task_last_duration_seconds",
        "How long each periodic task's last run took.",
        labels=("task",),
    )
    for name, record in sorted(snapshot.tasks.items()):
        runs.add_metric([name, "ok"], record.successes)
        runs.add_metric([name, "error"], record.failures)
        runs.add_metric([name, "skipped"], record.skipped)
        if record.last_success is not None:
            last_success.add_metric([name], record.last_success)
        if record.last_failure is not None:
            last_failure.add_metric([name], record.last_failure)
        if record.last_duration is not None:
            duration.add_metric([name], record.last_duration)

    http = CounterMetricFamily(
        "michel_http_requests",
        "Requests through the shared HTTP sessions, per host and outcome (2xx…5xx, or error "
        "when no response arrived). Each retry counts.",
        labels=("host", "outcome"),
    )
    for (host, outcome), count in sorted(snapshot.http_requests.items()):
        http.add_metric([host, outcome], count)

    background = CounterMetricFamily(
        "michel_background_task_failures",
        "Background tasks (spawn()) that ended with an exception.",
        labels=("name",),
    )
    for name, count in sorted(snapshot.background_failures.items()):
        background.add_metric([name], count)

    backup = GaugeMetricFamily(
        "michel_backup_last_success_timestamp_seconds",
        "When the newest complete database backup was taken.",
    )
    if snapshot.last_backup is not None:
        backup.add_metric([], snapshot.last_backup)

    return [runs, last_success, last_failure, duration, http, background, backup]


class HealthCollector(Collector):
    """Serves :func:`health_families` of whatever *source* returns at each scrape."""

    def __init__(self, source: Callable[[], HealthSnapshot]) -> None:
        self._source = source

    def describe(self) -> Iterator[Metric]:
        yield from health_families(HealthSnapshot())

    def collect(self) -> Iterator[Metric]:
        yield from health_families(self._source())


__all__ = ["HealthCollector", "health_families"]
