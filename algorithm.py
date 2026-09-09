from __future__ import annotations

from io import BytesIO, StringIO
from math import atan2, cos, radians, sin, sqrt
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances


REQUIRED_COLUMNS = {"point_id", "lat", "lon", "n_visits"}


def read_points_file(filename: str, content: bytes) -> pd.DataFrame:
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        return pd.read_csv(StringIO(content.decode("utf-8-sig")))
    if lower_name.endswith((".xlsx", ".xls")):
        return pd.read_excel(BytesIO(content))
    raise ValueError("Поддерживаются только CSV и Excel-файлы")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return radius_km * 2 * atan2(sqrt(a), sqrt(1 - a))


def _validate_and_clean(
    data: pd.DataFrame,
    n_working_days: int,
) -> tuple[pd.DataFrame, list[str]]:
    missing = REQUIRED_COLUMNS - set(data.columns)
    if missing:
        raise ValueError("В файле не хватает колонок: " + ", ".join(sorted(missing)))

    warnings: list[str] = []
    before = len(data)
    data = data.copy()
    data["lat"] = pd.to_numeric(data["lat"], errors="coerce")
    data["lon"] = pd.to_numeric(data["lon"], errors="coerce")
    data["n_visits"] = pd.to_numeric(data["n_visits"], errors="coerce")

    data = data.dropna(subset=["point_id", "lat", "lon", "n_visits"])
    data = data[
        np.isfinite(data["lat"])
        & np.isfinite(data["lon"])
        & data["lat"].between(-90, 90)
        & data["lon"].between(-180, 180)
        & (data["n_visits"] > 0)
        & (data["n_visits"] <= n_working_days)
    ].reset_index(drop=True)

    data["n_visits"] = data["n_visits"].astype(int)
    data["point_id"] = data["point_id"].astype(str)

    removed = before - len(data)
    if removed:
        warnings.append(f"Удалено строк после проверки данных: {removed}")
    if data.empty:
        raise ValueError("После проверки данных не осталось точек для планирования")
    return data, warnings


def _order_route_nearest_neighbor(group: pd.DataFrame) -> pd.DataFrame:
    group = group.copy().reset_index(drop=True)

    if len(group) <= 1:
        group["route_order"] = range(1, len(group) + 1)
        group["distance_from_prev_km"] = 0.0
        group["prev_lat"] = np.nan
        group["prev_lon"] = np.nan
        return group

    remaining = group.index.tolist()
    route = []
    current_idx = remaining.pop(0)
    route.append(current_idx)

    while remaining:
        current = group.loc[current_idx]
        next_idx = min(
            remaining,
            key=lambda idx: haversine_km(
                current["lat"],
                current["lon"],
                group.loc[idx, "lat"],
                group.loc[idx, "lon"],
            ),
        )
        remaining.remove(next_idx)
        route.append(next_idx)
        current_idx = next_idx

    ordered = group.loc[route].copy().reset_index(drop=True)
    ordered["route_order"] = range(1, len(ordered) + 1)
    ordered["prev_lat"] = ordered["lat"].shift()
    ordered["prev_lon"] = ordered["lon"].shift()
    ordered["distance_from_prev_km"] = ordered.apply(
        lambda row: 0.0
        if pd.isna(row["prev_lat"])
        else haversine_km(row["prev_lat"], row["prev_lon"], row["lat"], row["lon"]),
        axis=1,
    )
    return ordered


def plan_routes_algorithm_3(
    data: pd.DataFrame,
    n_managers: Optional[int] = None,
    max_visits_per_day: int = 12,
    n_working_days: int = 22,
    random_state: int = 42,
) -> dict[str, Any]:
    data_original, warnings = _validate_and_clean(data, n_working_days)

    n_managers_source = "manual"
    if n_managers is None:
        if "manager" in data_original.columns and data_original["manager"].nunique() > 0:
            n_managers = int(data_original["manager"].nunique())
            n_managers_source = "csv"
        else:
            n_managers = 3
            n_managers_source = "default"
            warnings.append("Колонка manager не найдена, использовано значение по умолчанию: 3")

    if n_managers < 1:
        raise ValueError("Количество менеджеров должно быть больше 0")
    if max_visits_per_day < 1:
        raise ValueError("Лимит визитов в день должен быть больше 0")
    if n_working_days < 1:
        raise ValueError("Количество рабочих дней должно быть больше 0")
    if n_managers > len(data_original):
        raise ValueError("Менеджеров больше, чем точек в файле")

    target_load = n_working_days * max_visits_per_day

    kmeans = KMeans(n_clusters=n_managers, random_state=random_state, n_init=10)
    kmeans.fit_predict(
        data_original[["lat", "lon"]],
        sample_weight=data_original["n_visits"],
    )

    centers = kmeans.cluster_centers_
    distances = pairwise_distances(data_original[["lat", "lon"]], centers)

    data_balanced = data_original.copy()
    data_balanced["nearest_cluster"] = distances.argmin(axis=1)
    data_balanced["nearest_distance"] = distances.min(axis=1)

    manager_load = {i: 0 for i in range(n_managers)}
    assigned_clusters: list[tuple[int, int]] = []

    order = data_balanced.sort_values(
        ["n_visits", "nearest_distance"],
        ascending=[False, False],
    ).index

    for idx in order:
        visits = int(data_balanced.loc[idx, "n_visits"])
        candidates = pd.Series(distances[idx], index=range(n_managers)).sort_values().index

        chosen_cluster = None
        for cluster_id in candidates:
            if manager_load[int(cluster_id)] + visits <= target_load:
                chosen_cluster = int(cluster_id)
                break

        if chosen_cluster is None:
            chosen_cluster = min(manager_load, key=lambda cluster_id: manager_load[cluster_id])

        assigned_clusters.append((idx, chosen_cluster))
        manager_load[chosen_cluster] += visits

    for idx, cluster_id in assigned_clusters:
        data_balanced.loc[idx, "cluster"] = cluster_id

    data_balanced["cluster"] = data_balanced["cluster"].astype(int)
    data_work = data_balanced.copy().reset_index(drop=True)
    data_work["row_id"] = data_work.index
    data_work["visits_done"] = 0
    data_work["visits_remain"] = data_work["n_visits"]

    selected_batches: list[pd.DataFrame] = []

    for day in range(1, n_working_days + 1):
        used_today: set[str] = set()

        for manager_id in range(n_managers):
            available = data_work[
                (data_work["visits_remain"] > 0)
                & (~data_work["point_id"].isin(used_today))
            ].copy()

            if available.empty:
                continue

            available["is_own_cluster"] = (available["cluster"] == manager_id).astype(int)
            available["is_new_point"] = (available["visits_done"] == 0).astype(int)

            candidates = available.sort_values(
                ["is_own_cluster", "is_new_point", "visits_remain"],
                ascending=[False, False, False],
            ).head(max_visits_per_day).copy()

            candidates["manager"] = manager_id
            candidates["day"] = day
            candidates = _order_route_nearest_neighbor(candidates)
            selected_batches.append(candidates)

            used_today.update(candidates["point_id"].tolist())
            visited_rows = candidates["row_id"]
            data_work.loc[data_work["row_id"].isin(visited_rows), "visits_done"] += 1
            data_work.loc[data_work["row_id"].isin(visited_rows), "visits_remain"] -= 1

    if selected_batches:
        selected_points_df = pd.concat(selected_batches, ignore_index=True)
    else:
        selected_points_df = pd.DataFrame()

    route_columns = [
        "point_id",
        "manager",
        "day",
        "route_order",
        "lat",
        "lon",
        "n_visits",
        "cluster",
        "distance_from_prev_km",
    ]
    routes = selected_points_df[route_columns].copy()

    summary = (
        routes.groupby("manager")
        .agg(
            visits=("point_id", "size"),
            unique_points=("point_id", "nunique"),
            distance_km=("distance_from_prev_km", "sum"),
        )
        .reset_index()
    )

    cluster_summary = (
        data_balanced.groupby("cluster")
        .agg(
            points=("point_id", "nunique"),
            required_visits=("n_visits", "sum"),
            avg_visits=("n_visits", "mean"),
        )
        .reset_index()
    )

    actual = routes.groupby("point_id").size().rename("visits_done")
    points = data_balanced.merge(actual, on="point_id", how="left")
    points["visits_done"] = points["visits_done"].fillna(0).astype(int)
    points["visits_remain"] = points["n_visits"] - points["visits_done"]

    total_required = int(data_balanced["n_visits"].sum())
    total_planned = int(len(routes))

    return {
        "warnings": warnings,
        "totals": {
            "points": int(data_balanced["point_id"].nunique()),
            "managers": int(n_managers),
            "managers_source": n_managers_source,
            "required_visits": total_required,
            "planned_visits": total_planned,
            "completion_percent": round(total_planned / total_required * 100, 1)
            if total_required
            else 0,
            "distance_km": round(float(routes["distance_from_prev_km"].sum()), 2)
            if not routes.empty
            else 0,
        },
        "summary": _records(summary, round_cols=["distance_km"]),
        "cluster_summary": _records(cluster_summary, round_cols=["avg_visits"]),
        "routes": _records(routes, round_cols=["distance_from_prev_km"]),
        "points": _records(
            points[
                [
                    "point_id",
                    "lat",
                    "lon",
                    "n_visits",
                    "cluster",
                    "visits_done",
                    "visits_remain",
                ]
            ]
        ),
    }


def _records(df: pd.DataFrame, round_cols: list[str] | None = None) -> list[dict[str, Any]]:
    prepared = df.copy()
    for col in round_cols or []:
        if col in prepared:
            prepared[col] = prepared[col].round(2)
    return prepared.replace({np.nan: None}).to_dict(orient="records")
