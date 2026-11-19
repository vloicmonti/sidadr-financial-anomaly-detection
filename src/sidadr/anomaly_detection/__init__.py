"""Module F — valeurs statistiquement atypiques."""

from sidadr.anomaly_detection.statistical import (
    IQR,
    ROBUST_Z,
    Anomaly,
    AnomalyReport,
    GroupStats,
    detect_anomalies,
)

__all__ = ["IQR", "ROBUST_Z", "Anomaly", "AnomalyReport", "GroupStats", "detect_anomalies"]
