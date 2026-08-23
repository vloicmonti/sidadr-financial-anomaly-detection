# SIDADR — Financial Anomaly & Duplicate Detection

> Automated ETL pipeline that caught data errors across 500,000+ regional financial records from 6 countries — before they reached decision-makers.

⚠️ **Data confidentiality**: built on proprietary, multi-source financial
data from BEAC (6 CEMAC member countries). Real data is **not included**
in this repository — the public version should run on a synthetic sample
dataset. See [Data](#data).

## Business Problem

A central bank aggregating financial data from 6 member countries through
multiple source systems faces a recurring risk: duplicate entries and
silent data errors that undermine the reliability of any downstream
reporting or policy decision built on that data.

## Solution

SIDADR (Système Intelligent de Détection des Anomalies et Duplications) is
an automated ETL pipeline that ingests multi-source regional financial
records, flags anomalies and duplicate entries, and produces a cleaned,
trustworthy dataset for decision-making.

## Architecture

```mermaid
flowchart LR
    A[Multi-source financial data<br/>6 CEMAC countries] --> B[ETL Ingestion]
    B --> C[Duplicate Detection]
    B --> D[Anomaly Detection]
    C --> E[Cleaned dataset]
    D --> E
    E --> F[Decision-ready financial data]
```

## Tech Stack

- **Language**: Python (Pandas, NumPy)
- **Database**: MySQL
- **Query**: SQL

## Data

- ~500,000+ multi-source financial records covering 6 CEMAC member
  countries (proprietary, BEAC — **not published**)

## Methodology

1. Ingest financial records from multiple regional source systems
2. Standardize schemas across sources
3. Detect duplicate records (entity resolution / matching rules)
4. Detect anomalies (rule-based and/or statistical outlier detection)
5. Produce a cleaned, validated dataset with an audit trail of
   corrections

## Results

- **-35% data error rate**
- Secured decision-quality data for 6 member countries

## Future Improvements

- Add automated tests covering duplicate/anomaly detection edge cases
- Add a lightweight dashboard for data quality monitoring over time
- Parameterize detection rules per data source

## Author

**Vincent Loïc Monti**
[LinkedIn](https://www.linkedin.com/in/vincent-lo%C3%AFc-monti-981641388/) · [loicmonti318@gmail.com](mailto:loicmonti318@gmail.com)
