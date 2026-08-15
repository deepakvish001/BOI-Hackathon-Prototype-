"""BOI alert-dataset track: the organisers' own schema, end to end.

The Phase-2 dataset is a wide tabular extract, one row per transaction-monitoring
alert, with 3,923 predictors and a ``FRAUD_TGT`` label. This package is
everything needed to go from their file to a scored submission:

``schema``   the published data dictionary, the feature grammar, and the
             resolution columns that leak the label
``dataset``  a loader that survives whatever format and column drift arrives
``features`` engineered columns that exist only *across* the supplied ones
``synth``    a stand-in table with the identical schema, so the pipeline is
             testable before the real data lands
``model``    the classifier, with feature selection performed inside every
             cross-validation fold

The engine in the rest of ``bodhi`` scores accounts from raw transactions; this
package scores the bank's own pre-aggregated alerts. They meet at the same
place: a calibrated risk with an explanation attached.
"""

from bodhi.boi.dataset import AlignmentReport, load_alerts, read_any
from bodhi.boi.model import BOIConfig, BOIModel
from bodhi.boi.schema import (
    LEAKAGE_COLUMNS,
    TARGET,
    DataDictionary,
    load_dictionary,
    parse_feature,
)

__all__ = [
    "load_dictionary", "DataDictionary", "parse_feature", "TARGET",
    "LEAKAGE_COLUMNS", "load_alerts", "read_any", "AlignmentReport",
    "BOIModel", "BOIConfig",
]
