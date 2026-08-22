#!/usr/bin/env python3
"""
British Columbia wildfire / hotspot history: 2017–2026

Creates TWO accumulated BC-wide images for each year:

    Image 1: May 1 through July 31
    Image 2: May 1 through September 30
             (therefore Image 2 includes everything in Image 1)

Total: 20 PNG images.

For the current year, if September 30 has not happened yet, Image 2
contains all available detections through today's date and is labelled
"season to date".

Data:
    NASA FIRMS VIIRS S-NPP 375 m active-fire / thermal detections.

Why only S-NPP?
    Using one satellite series for every year makes the 2017–2026
    comparison more consistent. Adding NOAA-20/NOAA-21 only in later
    years would make those later years look denser partly because more
    satellites were observing.

Boundary:
    Statistics Canada 2021 cartographic province boundary.
    British Columbia PRUID = 59.

Required environment variable / GitHub repository secret:
    FIRMS_MAP_KEY
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import contextily as ctx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from matplotlib.lines import Line2D
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as shapely_transform

TZ = ZoneInfo("America/Vancouver")
START_YEAR = 2017
END_YEAR = 2026
YEARS = list(range(START_YEAR, END_YEAR + 1))

# Whole British Columbia with a little padding.
# FIRMS order: west, south, east, north.
BC_BBOX = (-139.20, 48.20, -113.50, 60.10)

# One comparable sensor across every year.
SP_SOURCE = "VIIRS_SNPP_SP"
NRT_SOURCE = "VIIRS_SNPP_NRT"

# Different shade of red for every year.
YEAR_COLORS = {
    2017: "#FFD0D0",
    2018: "#FFB4B4",
    2019: "#FF9898",
    2020: "#FF7C7C",
    2021: "#FA6060",
    2022: "#EA4747",
    2023: "#D43232",
    2024: "#B91F1F",
    2025: "#941313",
    2026: "#6B0000",
}

POINT_SIZE = 7
POINT_ALPHA = 0.78
FRAME_DPI = 170
FIGSIZE = (13, 14)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data_bc_history"
OUTPUT_DIR = ROOT / "bc_fire_images_2017_2026"

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api"
FIRMS_AREA = FIRMS_BASE + "/area/csv"
FIRMS_AVAILABILITY = FIRMS_BASE + "/data_availability/csv"

STATCAN_BC_GEOJSON = (
    "https://geo.statcan.gc.ca/geo_wa/rest/services/"
    "2021/Cartographic_boundary_files/MapServer/0/query"
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "BC-Wildfire-History-2017-2026/1.0"})

PLACES = {
    "Victoria": (-123.3656, 48.4284),
    "Vancouver": (-123.1207, 49.2827),
    "Kelowna": (-119.4960, 49.8880),
    "Kamloops": (-120.3273, 50.6745),
    "Prince George": (-122.7497, 53.9171),
    "Williams Lake": (-122.1417, 52.1292),
    "Smithers": (-127.1743, 54.7804),
    "Fort St. John": (-120.8460, 56.2465),
    "Cranbrook": (-115.7688, 49.5120),
}

TO_WEB_MERCATOR = Transformer.from_crs(
    "EPSG:4326", "EPSG:3857", always_xy=True
)


def log(message: str) -> None:
    print(message, flush=True)


def local_today() -> date:
    return datetime.now(TZ).date()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_map_key() -> str:
    key = os.getenv("FIRMS_MAP_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FIRMS_MAP_KEY is missing. In GitHub go to Settings > Secrets and variables > "
            "Actions and add the repository secret FIRMS_MAP_KEY."
        )
    return key


def inclusive_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def get_bc_boundary():
    """Download official Statistics Canada BC cartographic boundary."""
    params = {
        "where": "PRUID='59'",
        "outFields": "PRUID,PRNAME",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }

    log("Downloading British Columbia boundary from Statistics Canada...")
    response = SESSION.get(STATCAN_BC_GEOJSON, params=params, timeout=120)
    response.raise_for_status()

    geojson = response.json()
    features = geojson.get("features", [])
    if not features:
        raise RuntimeError("Statistics Canada returned no British Columbia boundary.")

    return shape(features[0]["geometry"])


def points_inside_bc(df: pd.DataFrame, bc_geometry) -> pd.DataFrame:
    if df.empty:
        return df

    try:
        from shapely import contains_xy
        mask = contains_xy(
            bc_geometry,
            df["longitude"].to_numpy(),
            df["latitude"].to_numpy(),
        )
    except Exception:
        mask = np.array(
            [
                bc_geometry.contains(Point(lon, lat))
                for lon, lat in zip(df["longitude"], df["latitude"])
            ],
            dtype=bool,
        )

    return df.loc[mask].copy()


def get_availability(map_key: str) -> dict[str, tuple[date, date]]:
    url = f"{FIRMS_AVAILABILITY}/{map_key}/ALL"
    log("Checking NASA FIRMS data availability...")

    response = SESSION.get(url, timeout=90)
    response.raise_for_status()
    df = pd.read_csv(io.StringIO(response.text))

    availability: dict[str, tuple[date, date]] = {}

    for source in (SP_SOURCE, NRT_SOURCE):
        row = df[df["data_id"] == source]
        if row.empty:
            continue

        min_date = pd.to_datetime(row.iloc[0]["min_date"]).date()
        max_date = pd.to_datetime(row.iloc[0]["max_date"]).date()
        availability[source] = (min_date, max_date)
        log(f"{source}: {min_date} through {max_date}")

    if not availability:
        raise RuntimeError("FIRMS did not report S-NPP SP or NRT availability.")

    return availability


def source_for_day(day: date, availability: dict[str, tuple[date, date]]) -> str | None:
    # Prefer standard science-quality processing.
    if SP_SOURCE in availability:
        low, high = availability[SP_SOURCE]
        if low <= day <= high:
            return SP_SOURCE

    if NRT_SOURCE in availability:
        low, high = availability[NRT_SOURCE]
        if low <= day <= high:
            return NRT_SOURCE

    return None


def download_plan(start: date, end: date, availability: dict[str, tuple[date, date]]):
    """Build <=5-day requests, as required by the FIRMS Area API."""
    plan: list[tuple[date, int, str]] = []
    missing: list[date] = []
    days = list(inclusive_dates(start, end))
    i = 0

    while i < len(days):
        first_day = days[i]
        source = source_for_day(first_day, availability)

        if source is None:
            missing.append(first_day)
            i += 1
            continue

        count = 1
        while (
            i + count < len(days)
            and count < 5
            and source_for_day(days[i + count], availability) == source
            and days[i + count] == first_day + timedelta(days=count)
        ):
            count += 1

        plan.append((first_day, count, source))
        i += count

    return plan, missing


def firms_area_url(map_key: str, source: str, start: date, day_count: int) -> str:
    west, south, east, north = BC_BBOX
    area = f"{west},{south},{east},{north}"
    return f"{FIRMS_AREA}/{map_key}/{source}/{area}/{day_count}/{start.isoformat()}"


def fetch_firms_request(
    map_key: str, source: str, start: date, day_count: int
) -> pd.DataFrame:
    url = firms_area_url(map_key, source, start, day_count)
    log(f"  {source}: {start} for {day_count} day(s)")

    response = SESSION.get(url, timeout=120)
    response.raise_for_status()
    text = response.text.strip()

    if not text:
        return pd.DataFrame()

    if "latitude" not in text.lower() or "longitude" not in text.lower():
        preview = text[:300].replace("\n", " ")
        raise RuntimeError(f"Unexpected FIRMS response: {preview}")

    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        return df

    df.columns = [str(column).strip() for column in df.columns]
    df["source"] = source
    df["acq_date"] = pd.to_datetime(df["acq_date"], errors="coerce").dt.date
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude", "acq_date"])
    return df


def normalize_detections(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    dedup_columns = [
        name
        for name in (
            "latitude",
            "longitude",
            "acq_date",
            "acq_time",
            "satellite",
            "instrument",
        )
        if name in df.columns
    ]

    if dedup_columns:
        df = df.drop_duplicates(subset=dedup_columns, keep="last")

    return df.sort_values(["acq_date", "latitude", "longitude"]).reset_index(drop=True)


def fetch_year(
    year: int,
    map_key: str,
    availability: dict[str, tuple[date, date]],
    bc_geometry,
    today: date,
):
    requested_start = date(year, 5, 1)
    requested_end = date(year, 9, 30)
    actual_end = min(requested_end, today)

    cache_file = DATA_DIR / f"bc_viirs_snpp_{year}.csv"
    missing_file = DATA_DIR / f"bc_viirs_snpp_{year}_missing.json"

    # Completed historical years use cache after first successful run.
    if cache_file.exists() and year < today.year:
        log(f"{year}: using cached historical data.")
        df = pd.read_csv(cache_file)
        if not df.empty:
            df["acq_date"] = pd.to_datetime(df["acq_date"], errors="coerce").dt.date

        missing = []
        if missing_file.exists():
            try:
                payload = json.loads(missing_file.read_text(encoding="utf-8"))
                missing = [
                    date.fromisoformat(value)
                    for value in payload.get("missing_dates", [])
                ]
            except Exception:
                missing = []

        return df, missing, actual_end

    if actual_end < requested_start:
        return pd.DataFrame(), [], actual_end

    log(f"{year}: downloading {requested_start} through {actual_end}")
    plan, missing = download_plan(requested_start, actual_end, availability)
    frames: list[pd.DataFrame] = []

    for start, day_count, source in plan:
        try:
            part = fetch_firms_request(map_key, source, start, day_count)
            if not part.empty:
                frames.append(part)
        except Exception as exc:
            log(f"  WARNING: request failed for {start}: {exc}")
            for offset in range(day_count):
                missing.append(start + timedelta(days=offset))

    df = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )

    df = normalize_detections(df)
    if not df.empty:
        df = points_inside_bc(df, bc_geometry)

    save_df = df.copy()
    if not save_df.empty:
        save_df["acq_date"] = save_df["acq_date"].astype(str)
    save_df.to_csv(cache_file, index=False)

    missing = sorted(set(missing))
    missing_file.write_text(
        json.dumps(
            {
                "year": year,
                "requested_start": requested_start.isoformat(),
                "actual_end": actual_end.isoformat(),
                "missing_dates": [value.isoformat() for value in missing],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    log(f"{year}: {len(df):,} BC detections after province clipping.")
    if missing:
        log(f"{year}: WARNING — {len(missing)} requested date(s) unavailable from FIRMS.")

    return df, missing, actual_end


def transform_geometry_to_web_mercator(geometry):
    return shapely_transform(TO_WEB_MERCATOR.transform, geometry)


def plot_polygon_boundary(ax, geometry, linewidth=1.4):
    if geometry.geom_type == "Polygon":
        polygons = [geometry]
    elif geometry.geom_type == "MultiPolygon":
        polygons = list(geometry.geoms)
    else:
        polygons = []

    for polygon in polygons:
        x, y = polygon.exterior.xy
        ax.plot(x, y, color="black", linewidth=linewidth, zorder=20)
        for interior in polygon.interiors:
            ix, iy = interior.xy
            ax.plot(ix, iy, color="black", linewidth=0.5, zorder=20)


def projected_bc_extent():
    west, south, east, north = BC_BBOX
    x1, y1 = TO_WEB_MERCATOR.transform(west, south)
    x2, y2 = TO_WEB_MERCATOR.transform(east, north)
    return x1, x2, y1, y2


def add_places(ax):
    for name, (lon, lat) in PLACES.items():
        x, y = TO_WEB_MERCATOR.transform(lon, lat)
        ax.plot(x, y, marker="o", markersize=2.8, color="black", zorder=25)
        ax.annotate(
            name,
            (x, y),
            xytext=(4, 3),
            textcoords="offset points",
            fontsize=7.5,
            fontweight="bold",
            color="black",
            zorder=26,
            bbox=dict(facecolor="white", alpha=0.70, edgecolor="none", pad=1.0),
        )


def add_basemap(ax):
    try:
        ctx.add_basemap(
            ax,
            source=ctx.providers.Esri.WorldTopoMap,
            crs="EPSG:3857",
            zoom=5,
            attribution=False,
        )
    except Exception as exc:
        log(f"Basemap warning: {exc}")
        ax.set_facecolor("#edf1e8")


def period_data(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty:
        return df
    return df[(df["acq_date"] >= start) & (df["acq_date"] <= end)].copy()


def make_bc_image(
    year: int,
    period_name: str,
    period_start: date,
    period_end: date,
    target_end: date,
    df: pd.DataFrame,
    missing_dates: list[date],
    bc_geometry_3857,
):
    color = YEAR_COLORS[year]
    fig, ax = plt.subplots(figsize=FIGSIZE)

    x1, x2, y1, y2 = projected_bc_extent()
    ax.set_xlim(x1, x2)
    ax.set_ylim(y1, y2)
    ax.set_aspect("equal", adjustable="box")

    add_basemap(ax)

    polygons = (
        [bc_geometry_3857]
        if bc_geometry_3857.geom_type == "Polygon"
        else list(bc_geometry_3857.geoms)
    )

    for polygon in polygons:
        x, y = polygon.exterior.xy
        ax.fill(
            x,
            y,
            facecolor="white",
            alpha=0.10,
            edgecolor="none",
            zorder=8,
        )

    draw_df = period_data(df, period_start, period_end)

    if not draw_df.empty:
        xs, ys = TO_WEB_MERCATOR.transform(
            draw_df["longitude"].to_numpy(),
            draw_df["latitude"].to_numpy(),
        )
        ax.scatter(
            xs,
            ys,
            s=POINT_SIZE,
            color=color,
            alpha=POINT_ALPHA,
            linewidths=0,
            zorder=15,
        )

    plot_polygon_boundary(ax, bc_geometry_3857)
    add_places(ax)

    if period_end < target_end:
        period_text = (
            f"Accumulated {period_start.strftime('%B %d')} – "
            f"{period_end.strftime('%B %d, %Y')} (season to date)"
        )
    else:
        period_text = (
            f"Accumulated {period_start.strftime('%B %d')} – "
            f"{target_end.strftime('%B %d, %Y')}"
        )

    ax.set_title(
        f"British Columbia Wildfire Hotspots — {year}\n{period_text}",
        fontsize=19,
        fontweight="bold",
        pad=14,
    )

    legend_handle = Line2D(
        [0],
        [0],
        marker="o",
        linestyle="",
        markerfacecolor=color,
        markeredgecolor=color,
        markersize=8,
        label=f"{year} cumulative VIIRS S-NPP detections",
    )

    ax.legend(
        handles=[legend_handle],
        loc="lower left",
        fontsize=9,
        framealpha=0.88,
    )

    missing_in_period = [
        day for day in missing_dates if period_start <= day <= period_end
    ]

    footer = (
        "NASA FIRMS VIIRS S-NPP 375 m thermal detections. "
        "Points are satellite hotspots, not official wildfire perimeters. "
        "BC boundary: Statistics Canada."
    )

    if missing_in_period:
        footer += f" FIRMS unavailable for {len(missing_in_period)} day(s) in this period."

    fig.text(0.5, 0.018, footer, ha="center", va="bottom", fontsize=7.5)

    ax.set_axis_off()
    fig.subplots_adjust(left=0.02, right=0.98, top=0.93, bottom=0.055)

    filename = OUTPUT_DIR / f"bc_fires_{year}_{period_name}.png"
    fig.savefig(filename, dpi=FRAME_DPI, facecolor="white")
    plt.close(fig)

    log(f"Created {filename.name} ({len(draw_df):,} detections)")


def main() -> int:
    ensure_dirs()
    map_key = get_map_key()
    today = local_today()

    bc_geometry = get_bc_boundary()
    bc_geometry_3857 = transform_geometry_to_web_mercator(bc_geometry)
    availability = get_availability(map_key)

    summary_rows = []

    for year in YEARS:
        if year > today.year:
            continue

        df, missing, _ = fetch_year(
            year,
            map_key,
            availability,
            bc_geometry,
            today,
        )

        may_1 = date(year, 5, 1)
        july_31 = date(year, 7, 31)
        sep_30 = date(year, 9, 30)

        # IMAGE 1: May 1 through July 31 accumulated.
        image1_end = min(july_31, today)
        if image1_end >= may_1:
            make_bc_image(
                year=year,
                period_name="may_jul",
                period_start=may_1,
                period_end=image1_end,
                target_end=july_31,
                df=df,
                missing_dates=missing,
                bc_geometry_3857=bc_geometry_3857,
            )

        # IMAGE 2: May 1 through September 30 accumulated.
        # This includes Image 1 plus August and September.
        image2_end = min(sep_30, today)
        if image2_end >= may_1:
            make_bc_image(
                year=year,
                period_name="may_sep",
                period_start=may_1,
                period_end=image2_end,
                target_end=sep_30,
                df=df,
                missing_dates=missing,
                bc_geometry_3857=bc_geometry_3857,
            )

        image1_count = (
            len(period_data(df, may_1, image1_end)) if image1_end >= may_1 else 0
        )
        image2_count = (
            len(period_data(df, may_1, image2_end)) if image2_end >= may_1 else 0
        )

        summary_rows.append(
            {
                "year": year,
                "shade": YEAR_COLORS[year],
                "may_to_july_detections": image1_count,
                "may_to_september_or_current_detections": image2_count,
                "missing_firms_days": len(missing),
                "data_through": image2_end.isoformat() if image2_end >= may_1 else "",
            }
        )

    summary_file = OUTPUT_DIR / "bc_fire_history_summary.csv"
    pd.DataFrame(summary_rows).to_csv(summary_file, index=False)

    log("")
    log("DONE")
    log(f"Images are in: {OUTPUT_DIR}")
    log(f"Summary: {summary_file.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise
