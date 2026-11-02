#!/usr/bin/env python3
"""Offline aggregate report from a *manually verified* partner export.

Never connects to a partner API or PostgreSQL. It cannot verify report authenticity.
Never reads booking/customer-level rows; only the normalized aggregate template.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED = {"provider", "period_start", "period_end", "confirmed_bookings",
            "approved_commission", "currency", "report_reference"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("partner_csv", type=Path, help="Verified aggregate export manually normalized to the documented columns")
    args = p.parse_args()
    with args.partner_csv.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if set(reader.fieldnames or []) != REQUIRED:
            raise SystemExit("STOP: Unexpected columns, do not supply raw customer exports")
        totals: dict[tuple[str,str],tuple[int, Decimal]] = {}
        rows = 0
        for row in reader:
            if row["provider"] not in {"kiwitaxi", "discovercars", "viator", "concierge"}:
                raise SystemExit("STOP: Unknown provider")
            a,b = date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])
            if a>b: raise SystemExit("STOP: Invalid period")
            currency = row["currency"].strip().upper()
            if len(currency)!=3 or not currency.isalpha(): raise SystemExit("STOP: Invalid currency")
            if not row["report_reference"] or len(row["report_reference"])>128:
                raise SystemExit("STOP: Missing partner-report reference")
            try:
                n=int(row["confirmed_bookings"])
                amount=Decimal(row["approved_commission"])
            except (ValueError, InvalidOperation) as e:
                raise SystemExit("STOP: Invalid amount") from e
            if n<0 or amount<0 or not amount.is_finite(): raise SystemExit("STOP: Negative/nonfinite amounts")
            key=row["provider"],currency
            count,total=totals.get(key,(0,Decimal("0")))
            totals[key]=(count+n,total+amount)
            rows+=1
    if rows==0:
        print("PARTNER_REPORT_ROWS=0 (no claims of bookings or commissions)")
    for (provider,currency),(count,amount) in sorted(totals.items()):
        print(f"PARTNER_REPORTED provider={provider} confirmed_bookings={count} "
              f"approved_commission={amount} currency={currency}")
    print("CAUTION=Partner-reported aggregates; no per-click booking attribution")

if __name__=="__main__":
    main()
