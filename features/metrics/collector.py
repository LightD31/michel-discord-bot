"""A Prometheus collector that serves pre-computed samples.

Scrapes run on ``prometheus_client``'s HTTP thread. Reading MongoDB or the
bot's state from there would cross into the bot loop, so the metrics
extension computes everything on the bot loop and publishes it here with
:meth:`SnapshotCollector.publish`, which swaps one attribute — a scrape sees
either the old snapshot or the new one, never half of each.

Each scrape rebuilds the families from the current snapshot, so a series that
is no longer reported (a member who left the top N) disappears instead of
lingering with its last value, as a labelled ``Gauge`` would.
"""

from collections.abc import Callable, Iterable, Iterator

from prometheus_client.metrics_core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from features.metrics.samples import MetricSpec, Sample


class SnapshotCollector(Collector):
    """Serves the last published snapshot plus whatever ``live`` returns per scrape.

    ``live`` must be cheap and thread-safe (plain attribute reads): it runs on
    the scrape thread. Every spec is emitted on every scrape, with no samples
    when nothing reports it. Samples whose name has no spec are dropped.
    """

    def __init__(
        self,
        specs: Iterable[MetricSpec],
        live: Callable[[], Iterable[Sample]] | None = None,
    ) -> None:
        self._specs = tuple(specs)
        self._live = live
        self._snapshot: tuple[Sample, ...] = ()

    def publish(self, samples: Iterable[Sample]) -> None:
        self._snapshot = tuple(samples)

    def describe(self) -> Iterator[Metric]:
        for spec in self._specs:
            yield GaugeMetricFamily(spec.name, spec.documentation, labels=spec.labels)

    def collect(self) -> Iterator[Metric]:
        samples = list(self._snapshot)
        if self._live is not None:
            samples.extend(self._live())
        by_name: dict[str, list[Sample]] = {}
        for sample in samples:
            by_name.setdefault(sample.name, []).append(sample)
        for spec in self._specs:
            family = GaugeMetricFamily(spec.name, spec.documentation, labels=spec.labels)
            for sample in by_name.get(spec.name, []):
                labels = dict(sample.labels)
                family.add_metric([labels.get(label, "") for label in spec.labels], sample.value)
            yield family


__all__ = ["SnapshotCollector"]
