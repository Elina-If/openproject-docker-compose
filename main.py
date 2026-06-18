import os
import re
from datetime import date, datetime, timedelta
from math import ceil
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


OPENPROJECT_BASE_URL = os.getenv("OPENPROJECT_BASE_URL", "http://localhost:8080/api/v3")
OPENPROJECT_API_KEY = os.getenv(
	"OPENPROJECT_API_KEY",
	"opapi-99b19dcb0a1ad47170f7c50d2050a5b15d5fab000aa42fefbca7ad4d77040dd0",
)
OPENPROJECT_USERNAME = os.getenv("OPENPROJECT_USERNAME", "")
OPENPROJECT_PASSWORD = os.getenv("OPENPROJECT_PASSWORD", "")
TARGET_PROJECT_ID = 3
LEFT_OFFSET = 220
TOP_OFFSET = 64
ROW_HEIGHT = 96
DAY_WIDTH = 86
NODE_HEIGHT = 62
ROW_TASK_GAP = 28


app = FastAPI(title="OpenProject Bridge API")

app.add_middleware(
	CORSMiddleware,
	allow_origins=["http://localhost:3000", "http://localhost:5173"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


def rich_text_to_string(value: Any) -> str | None:
	if isinstance(value, str):
		return value
	if isinstance(value, dict):
		raw = value.get("raw")
		if isinstance(raw, str):
			return raw
		html = value.get("html")
		if isinstance(html, str):
			return html
	return None


def openproject_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
	url = f"{OPENPROJECT_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
	auth: tuple[str, str] | None = None

	if OPENPROJECT_API_KEY:
		auth = ("apikey", OPENPROJECT_API_KEY)
	elif OPENPROJECT_USERNAME and OPENPROJECT_PASSWORD:
		auth = (OPENPROJECT_USERNAME, OPENPROJECT_PASSWORD)

	try:
		response = requests.get(
			url,
			params=params,
			auth=auth,
			timeout=20,
		)

		if response.status_code in (401, 403):
			raise HTTPException(
				status_code=401,
				detail=(
					"OpenProject Auth fehlgeschlagen. Setze OPENPROJECT_API_KEY oder OPENPROJECT_USERNAME/OPENPROJECT_PASSWORD."
				),
			)

		response.raise_for_status()
		return response.json()
	except requests.RequestException as exc:
		raise HTTPException(status_code=502, detail=f"OpenProject API Fehler: {exc}") from exc


def fetch_project_work_packages(project_id: int) -> list[dict[str, Any]]:
	filters = {
		"filters": f'[{{"project":{{"operator":"=","values":["{project_id}"]}}}}]'
	}
	payload = openproject_get("work_packages", params=filters)
	return payload.get("_embedded", {}).get("elements", [])


def fetch_work_package_relations(work_package_id: int) -> list[dict[str, Any]]:
	payload = openproject_get(f"work_packages/{work_package_id}/relations")
	return payload.get("_embedded", {}).get("elements", [])


def parse_iso_date(value: str | None) -> date | None:
	if not value:
		return None
	try:
		return datetime.fromisoformat(value).date()
	except ValueError:
		return None


def parse_openproject_duration_days(value: str | None) -> int | None:
	if not value or not isinstance(value, str):
		return None

	# OpenProject liefert i. d. R. ISO-8601-Perioden wie P3D oder P2W.
	match = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?", value)
	if not match:
		return None

	weeks = int(match.group(1) or 0)
	days = int(match.group(2) or 0)
	total = weeks * 7 + days

	return total if total > 0 else None


def task_duration_days(duration_raw: str | None, start_date: str | None, due_date: str | None) -> int:
	duration_from_api = parse_openproject_duration_days(duration_raw)
	if duration_from_api is not None:
		return duration_from_api

	start = parse_iso_date(start_date)
	end = parse_iso_date(due_date)
	if start and end and end >= start:
		return (end - start).days + 1
	return 1


def order_row_tasks_right_to_left(
	row_task_ids: list[int],
	task_by_id: dict[int, dict[str, Any]],
	predecessors: dict[int, set[int]],
) -> list[int]:
	row_set = set(row_task_ids)
	in_degree: dict[int, int] = {}
	local_successors: dict[int, set[int]] = {task_id: set() for task_id in row_task_ids}

	for task_id in row_task_ids:
		local_preds = {pred for pred in predecessors.get(task_id, set()) if pred in row_set}
		in_degree[task_id] = len(local_preds)
		for pred in local_preds:
			local_successors.setdefault(pred, set()).add(task_id)

	def sort_key(task_id: int) -> tuple[date, int]:
		start = parse_iso_date(task_by_id[task_id].get("startDate")) or date.min
		return (start, task_id)

	available = sorted([task_id for task_id in row_task_ids if in_degree[task_id] == 0], key=sort_key)
	ordered: list[int] = []

	while available:
		current = available.pop(0)
		ordered.append(current)

		for successor in sorted(local_successors.get(current, set()), key=sort_key):
			in_degree[successor] -= 1
			if in_degree[successor] == 0:
				available.append(successor)
		available.sort(key=sort_key)

	if len(ordered) == len(row_task_ids):
		return ordered

	remaining = [task_id for task_id in row_task_ids if task_id not in set(ordered)]
	remaining.sort(key=sort_key)
	return ordered + remaining


def build_network_payload(project_id: int) -> dict[str, Any]:
	items = fetch_project_work_packages(project_id)

	tasks: list[dict[str, Any]] = []
	for item in items:
		assignee_link = item.get("_links", {}).get("assignee")
		assignee = assignee_link.get("title") if isinstance(assignee_link, dict) else "Unassigned"
		duration_raw = item.get("duration")
		duration = task_duration_days(duration_raw, item.get("startDate"), item.get("dueDate"))
		start_date = item.get("startDate")
		start_obj = parse_iso_date(start_date)
		effective_end = (start_obj + timedelta(days=duration - 1)).isoformat() if start_obj else item.get("dueDate")
		tasks.append(
			{
				"id": int(item.get("id")),
				"subject": item.get("subject") or "Ohne Titel",
				"assignee": assignee or "Unassigned",
				"startDate": start_date,
				"dueDate": effective_end,
				"durationRaw": duration_raw,
				"durationDays": duration,
			}
		)

	resources = sorted({task["assignee"] for task in tasks}) or ["Unassigned"]
	resource_index = {name: idx for idx, name in enumerate(resources)}
	task_by_id = {int(task["id"]): task for task in tasks}
	known_ids = set(task_by_id.keys())

	predecessors: dict[int, set[int]] = {task_id: set() for task_id in known_ids}
	successors: dict[int, set[int]] = {task_id: set() for task_id in known_ids}
	relation_pairs: set[tuple[int, int]] = set()

	for task in tasks:
		task_id = int(task["id"])
		relations = fetch_work_package_relations(task_id)
		for relation in relations:
			from_href = relation.get("_links", {}).get("from", {}).get("href", "")
			to_href = relation.get("_links", {}).get("to", {}).get("href", "")
			if not from_href or not to_href:
				continue

			try:
				source_id = int(from_href.rstrip("/").split("/")[-1])
				target_id = int(to_href.rstrip("/").split("/")[-1])
			except ValueError:
				continue

			if source_id not in known_ids or target_id not in known_ids:
				continue

			relation_pairs.add((source_id, target_id))
			successors[source_id].add(target_id)
			predecessors[target_id].add(source_id)

	starts = [parse_iso_date(task["startDate"]) for task in tasks if parse_iso_date(task["startDate"]) is not None]
	ends = [parse_iso_date(task["dueDate"]) for task in tasks if parse_iso_date(task["dueDate"]) is not None]

	base_date = min(starts) if starts else None
	last_date = max([d for d in ends if d is not None], default=base_date)

	timeline: list[str] = []
	if base_date and last_date:
		cursor = base_date
		while cursor <= last_date:
			timeline.append(cursor.isoformat())
			cursor += timedelta(days=1)

	required_columns = max(len(timeline), 1)
	for resource in resources:
		row_tasks = [task for task in tasks if task["assignee"] == resource]
		if not row_tasks:
			continue

		row_pixel_width = 0
		for index, row_task in enumerate(row_tasks):
			row_pixel_width += max(100, DAY_WIDTH * int(row_task["durationDays"]) - 10)
			if index < len(row_tasks) - 1:
				row_pixel_width += ROW_TASK_GAP

		required_columns = max(required_columns, ceil(row_pixel_width / DAY_WIDTH) + 1)

	row_right_anchor = LEFT_OFFSET + required_columns * DAY_WIDTH - 20

	nodes: list[dict[str, Any]] = []
	node_map: dict[int, dict[str, Any]] = {}
	for resource in resources:
		row_index = resource_index.get(resource, 0)
		row_y = TOP_OFFSET + row_index * ROW_HEIGHT
		row_task_ids = [int(task["id"]) for task in tasks if task["assignee"] == resource]
		ordered_ids = order_row_tasks_right_to_left(row_task_ids, task_by_id, predecessors)

		cursor_right = row_right_anchor
		for task_id in ordered_ids:
			task = task_by_id[task_id]
			duration = int(task["durationDays"])
			width = max(100, DAY_WIDTH * duration - 10)
			node = {
				"id": str(task["id"]),
				"label": task["subject"],
				"assignee": task["assignee"],
				"startDate": task["startDate"],
				"dueDate": task["dueDate"],
				"durationDays": duration,
				"x": cursor_right - width,
				"y": row_y,
				"width": width,
				"height": NODE_HEIGHT,
			}
			nodes.append(node)
			node_map[task_id] = node
			cursor_right = node["x"] - ROW_TASK_GAP

	edges: list[dict[str, Any]] = []
	for source_id, target_id in sorted(relation_pairs):
			source_node = node_map.get(source_id)
			target_node = node_map.get(target_id)
			if not source_node or not target_node:
				continue

			edges.append(
				{
					"id": f"{source_id}-{target_id}",
					"source": str(source_id),
					"target": str(target_id),
					"x1": source_node["x"] + source_node["width"],
					"y1": source_node["y"] + source_node["height"] / 2,
					"x2": target_node["x"],
					"y2": target_node["y"] + target_node["height"] / 2,
				}
			)

	return {
		"projectId": project_id,
		"resources": resources,
		"timeline": timeline,
		"layout": {
			"leftOffset": LEFT_OFFSET,
			"topOffset": TOP_OFFSET,
			"rowHeight": ROW_HEIGHT,
			"dayWidth": DAY_WIDTH,
			"nodeHeight": NODE_HEIGHT,
			"rowTaskGap": ROW_TASK_GAP,
			"rowRightAnchor": row_right_anchor,
		},
		"nodes": nodes,
		"edges": edges,
	}


@app.get("/")
def root() -> dict[str, str]:
	return {"status": "ok", "message": "Backend laeuft"}


@app.get("/project/3")
def get_project_3() -> dict[str, Any]:
	project = openproject_get(f"projects/{TARGET_PROJECT_ID}")

	return {
		"id": project.get("id", TARGET_PROJECT_ID),
		"identifier": project.get("identifier"),
		"name": project.get("name"),
		"description": rich_text_to_string(project.get("description")),
		"status": rich_text_to_string(project.get("statusExplanation")),
	}


@app.get("/project/3/work-packages")
def get_project_3_work_packages() -> list[dict[str, Any]]:
	items = fetch_project_work_packages(TARGET_PROJECT_ID)
	result: list[dict[str, Any]] = []

	for item in items:
		assignee_link = item.get("_links", {}).get("assignee")
		assignee = assignee_link.get("title") if isinstance(assignee_link, dict) else "Unassigned"
		duration_raw = item.get("duration")
		duration = task_duration_days(duration_raw, item.get("startDate"), item.get("dueDate"))
		start_date = item.get("startDate")
		start_obj = parse_iso_date(start_date)
		effective_end = (start_obj + timedelta(days=duration - 1)).isoformat() if start_obj else item.get("dueDate")

		result.append(
			{
				"id": item.get("id"),
				"subject": item.get("subject"),
				"assignee": assignee,
				"startDate": start_date,
				"dueDate": effective_end,
				"duration": duration_raw,
				"durationDays": duration,
			}
		)

	return sorted(result, key=lambda wp: wp.get("id") or 0)


@app.get("/project/3/network")
def get_project_3_network() -> dict[str, Any]:
	return build_network_payload(TARGET_PROJECT_ID)
