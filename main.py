import os
import re
from datetime import date, datetime, timedelta
from typing import Any
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

OPENPROJECT_BASE_URL = os.getenv("OPENPROJECT_BASE_URL", "http://localhost:8080/api/v3")
OPENPROJECT_API_KEY = os.getenv(
	"OPENPROJECT_API_KEY",
	"opapi-99b19dcb0a1ad47170f7c50d2050a5b15d5fab000aa42fefbca7ad4d77040dd0",
)

TARGET_PROJECT_ID = 3

#Layout constants for network diagram display
LEFT_OFFSET = 220
TOP_OFFSET = 80
ROW_HEIGHT = 92
DAY_WIDTH = 86
NODE_HEIGHT = 62
ROW_TASK_GAP = 28
STEP_X_GAP = 260 #shifting network plan level to the right


app = FastAPI(title="OpenProject API")

app.add_middleware(
	CORSMiddleware,
	allow_origins=["http://localhost:3000"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

#Convert rich text values into plain strings
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

#Encapsulate OpenProject API GET requests
def openproject_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
	url = f"{OPENPROJECT_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
	auth: tuple[str, str] | None = None

	if OPENPROJECT_API_KEY:
		auth = ("apikey", OPENPROJECT_API_KEY)

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
					"OpenProject Auth fehlgeschlagen. Setze OPENPROJECT_API_KEY."
				),
			)

		response.raise_for_status()
		return response.json()
	except requests.RequestException as exc:
		raise HTTPException(status_code=502, detail=f"OpenProject API Fehler: {exc}") from exc

#UC1, UC2, UC3: Fetch and return work packages
def fetch_project_work_packages(project_id: int) -> list[dict[str, Any]]:
	filters = {
		"filters": f'[{{"project":{{"operator":"=","values":["{project_id}"]}}}}]'
	}
	payload = openproject_get("work_packages", params=filters)
	return payload.get("_embedded", {}).get("elements", [])

#UC2, UC4: Fetch and return project memberships
def fetch_project_memberships(project_id: int) -> list[dict[str, Any]]:
	try:
		payload = openproject_get(f"projects/{project_id}/memberships", params={"pageSize": "200"})
		return payload.get("_embedded", {}).get("elements", [])
	except HTTPException as exc:
		if exc.status_code != 502 or "404" not in str(exc.detail):
			raise

	filters = {
		"filters": f'[{{"project":{{"operator":"=","values":["{project_id}"]}}}}]',
		"pageSize": "200",
	}
	payload = openproject_get("memberships", params=filters)
	return payload.get("_embedded", {}).get("elements", [])

#UC1, UC3: Fetch and return work package relations
def fetch_work_package_relations(work_package_id: int) -> list[dict[str, Any]]:
	payload = openproject_get(f"work_packages/{work_package_id}/relations")
	return payload.get("_embedded", {}).get("elements", [])

#UC3, UC4: Parse string into python date
def parse_iso_date(value: str | None) -> date | None:
	if not value:
		return None
	try:
		return datetime.fromisoformat(value).date()
	except ValueError:
		return None

#UC3: Convert OpenProject duration values (ISO-) into working days
def parse_openproject_duration_days(value: str | None) -> int | None:
	if not value or not isinstance(value, str):
		return None

	match = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?", value)
	if not match:
		return None

	weeks = int(match.group(1) or 0)
	days = int(match.group(2) or 0)
	total = weeks * 7 + days

	return total if total > 0 else None


def task_duration_days(duration_raw: str | None, start_date: str | None, due_date: str | None) -> int:
	#UC3: Determine the effective task duration for schedule calculations.
	duration_from_api = parse_openproject_duration_days(duration_raw)
	if duration_from_api is not None:
		return duration_from_api

	start = parse_iso_date(start_date)
	end = parse_iso_date(due_date)
	if start and end and end >= start:
		return (end - start).days + 1
	return 1

#UC1, UC3: Convert OpenProject relations into directed edges for network planning
def relation_to_dependency_edge(relation: dict[str, Any]) -> tuple[int, int] | None:
	from_href = relation.get("_links", {}).get("from", {}).get("href", "")
	to_href = relation.get("_links", {}).get("to", {}).get("href", "")
	if not from_href or not to_href:
		return None

	try:
		from_id = int(from_href.rstrip("/").split("/")[-1])
		to_id = int(to_href.rstrip("/").split("/")[-1])
	except ValueError:
		return None

	relation_type = str(relation.get("type") or "").lower()

	if relation_type == "follows":
		return (to_id, from_id)

	if relation_type in {"precedes", ""}:
		return (from_id, to_id)

	return None


def build_network_payload(project_id: int) -> dict[str, Any]:
	#UC1: Build the network payload for structure visualization.
	#UC3: Add scheduling data for path and timeline analysis.
	#UC4: Add resource data for utilization visualization.
	items = fetch_project_work_packages(project_id)

	tasks: list[dict[str, Any]] = []
	for item in items:
		#UC1: Prepare reusable task data for the network nodes.
		#UC3: Prepare reusable duration and date data for schedule analysis.
		#UC4: Prepare reusable assignee data for utilization views.
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

	#UC2: Build the resource list for allocation interactions.
	#UC4: Build the resource list for utilization views.
	resources = sorted({task["assignee"] for task in tasks}) or ["Unassigned"]
	task_by_id = {int(task["id"]): task for task in tasks}
	known_ids = set(task_by_id.keys())

	predecessors: dict[int, set[int]] = {task_id: set() for task_id in known_ids}
	successors: dict[int, set[int]] = {task_id: set() for task_id in known_ids}
	relation_pairs: set[tuple[int, int]] = set()

	for task in tasks:
		task_id = int(task["id"])
		relations = fetch_work_package_relations(task_id)
		for relation in relations:
			edge = relation_to_dependency_edge(relation)
			if edge is None:
				continue

			source_id, target_id = edge

			if source_id not in known_ids or target_id not in known_ids:
				continue

			#UC1: Convert relations into network edges.
			#UC3: Convert relations into dependency edges for schedule logic.
			relation_pairs.add((source_id, target_id))
			successors[source_id].add(target_id)
			predecessors[target_id].add(source_id)

	#UC1: Derive network levels from predecessor depth.
	queue = sorted([task_id for task_id in known_ids if len(predecessors[task_id]) == 0])
	in_degree = {task_id: len(predecessors[task_id]) for task_id in known_ids}
	topo_order: list[int] = []

	while queue:
		current = queue.pop(0)
		topo_order.append(current)
		for successor in sorted(successors.get(current, set())):
			in_degree[successor] -= 1
			if in_degree[successor] == 0:
				queue.append(successor)
		queue.sort()

	if len(topo_order) < len(known_ids):
		remaining = sorted([task_id for task_id in known_ids if task_id not in set(topo_order)])
		topo_order.extend(remaining)

	levels: dict[int, int] = {task_id: 0 for task_id in known_ids}
	for task_id in topo_order:
		for successor in successors.get(task_id, set()):
			levels[successor] = max(levels[successor], levels[task_id] + 1)

	level_to_tasks: dict[int, list[int]] = {}
	for task_id in known_ids:
		level_to_tasks.setdefault(levels[task_id], []).append(task_id)

	for level in level_to_tasks:
		level_to_tasks[level].sort()

	roots = sorted([task_id for task_id in known_ids if len(predecessors[task_id]) == 0])

	nodes: list[dict[str, Any]] = []
	node_map: dict[int, dict[str, Any]] = {}

	for level in sorted(level_to_tasks.keys()):
		task_ids = level_to_tasks[level]

		if level == 0:
			#UC1: Root tasks define the first network column.
			ordered_task_ids = task_ids
			preferred_y_by_task = {
				task_id: TOP_OFFSET + index * ROW_HEIGHT
				for index, task_id in enumerate(ordered_task_ids)
			}
		else:
			def preferred_y(task_id: int) -> float:
				preds = sorted(predecessors.get(task_id, set()))
				pred_centers = [
					node_map[pred_id]["y"] + NODE_HEIGHT / 2
					for pred_id in preds
					if pred_id in node_map
				]
				if pred_centers:
					return (sum(pred_centers) / len(pred_centers)) - NODE_HEIGHT / 2
				return TOP_OFFSET

			preferred_y_by_task = {task_id: preferred_y(task_id) for task_id in task_ids}
			ordered_task_ids = sorted(task_ids, key=lambda task_id: (preferred_y_by_task[task_id], task_id))

		placed_y: list[float] = []
		for task_id in ordered_task_ids:
			task = task_by_id[task_id]
			duration = int(task["durationDays"])
			width = max(110, DAY_WIDTH * duration - 10)
			x = LEFT_OFFSET + level * STEP_X_GAP
			preferred_top = max(TOP_OFFSET, preferred_y_by_task.get(task_id, TOP_OFFSET))

			#UC1: Shift nodes only enough to avoid overlap within the same column.
			y = preferred_top
			if placed_y:
				min_y = placed_y[-1] + ROW_HEIGHT
				y = max(y, min_y)

			placed_y.append(y)

			node = {
				"id": str(task_id),
				"label": task["subject"],
				"assignee": task["assignee"],
				"startDate": task["startDate"],
				"dueDate": task["dueDate"],
				"durationDays": duration,
				"x": x,
				"y": int(y),
				"width": width,
				"height": NODE_HEIGHT,
			}
			nodes.append(node)
			node_map[task_id] = node

	#UC1: Add start and end nodes to frame the network visually.
	start_y = TOP_OFFSET
	if roots:
		root_centers = [node_map[root_id]["y"] + NODE_HEIGHT / 2 for root_id in roots if root_id in node_map]
		if root_centers:
			start_y = int(sum(root_centers) / len(root_centers) - NODE_HEIGHT / 2)

	start_node = {
		"id": "START",
		"label": "Startpunkt",
		"assignee": "",
		"startDate": None,
		"dueDate": None,
		"durationDays": 0,
		"x": max(40, LEFT_OFFSET - 170),
		"y": start_y,
		"width": 120,
		"height": NODE_HEIGHT,
		"isStart": True,
	}
	nodes.append(start_node)

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

	for root_id in roots:
		target_node = node_map.get(root_id)
		if not target_node:
			continue
		edges.append(
			{
				"id": f"START-{root_id}",
				"source": "START",
				"target": str(root_id),
				"x1": start_node["x"] + start_node["width"],
				"y1": start_node["y"] + start_node["height"] / 2,
				"x2": target_node["x"],
				"y2": target_node["y"] + target_node["height"] / 2,
			}
		)

	max_level = max(level_to_tasks.keys(), default=0)

	sinks = sorted([task_id for task_id in known_ids if len(successors.get(task_id, set())) == 0])
	end_y = TOP_OFFSET
	if sinks:
		sink_centers = [node_map[sink_id]["y"] + NODE_HEIGHT / 2 for sink_id in sinks if sink_id in node_map]
		if sink_centers:
			end_y = int(sum(sink_centers) / len(sink_centers) - NODE_HEIGHT / 2)

	rightmost_sink_right = max(
		[node_map[sink_id]["x"] + node_map[sink_id]["width"] for sink_id in sinks if sink_id in node_map],
		default=LEFT_OFFSET + max_level * STEP_X_GAP,
	)
	end_node_x = rightmost_sink_right + 140
	end_node = {
		"id": "END",
		"label": "Endpunkt",
		"assignee": "",
		"startDate": None,
		"dueDate": None,
		"durationDays": 0,
		"x": end_node_x,
		"y": end_y,
		"width": 120,
		"height": NODE_HEIGHT,
		"isEnd": True,
	}
	nodes.append(end_node)

	for sink_id in sinks:
		source_node = node_map.get(sink_id)
		if not source_node:
			continue
		edges.append(
			{
				"id": f"{sink_id}-END",
				"source": str(sink_id),
				"target": "END",
				"x1": source_node["x"] + source_node["width"],
				"y1": source_node["y"] + source_node["height"] / 2,
				"x2": end_node["x"],
				"y2": end_node["y"] + end_node["height"] / 2,
			}
		)
	timeline = [f"Schritt {step + 1}" for step in range(max_level + 1)]

	return {
		"projectId": project_id,
		"resources": resources,
		"timeline": timeline,
		"layout": {
			"leftOffset": LEFT_OFFSET,
			"topOffset": TOP_OFFSET,
			"rowHeight": ROW_HEIGHT,
			"dayWidth": STEP_X_GAP,
			"nodeHeight": NODE_HEIGHT,
			"rowTaskGap": ROW_TASK_GAP,
			"rowRightAnchor": LEFT_OFFSET + max_level * STEP_X_GAP,
		},
		"nodes": nodes,
		"edges": edges,
	}

#REST endpoints
#Backend availability test
@app.get("/")
def root() -> dict[str, str]:
	return {"status": "ok", "message": "Backend laeuft"}

#UC1: Return project metadata
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

#UC2, UC3: Return work packages for interactive resource allocation and schedule analysis
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
				"description": rich_text_to_string(item.get("description")),
				"assignee": assignee,
				"startDate": start_date,
				"dueDate": effective_end,
				"duration": duration_raw,
				"durationDays": duration,
			}
		)

	return sorted(result, key=lambda wp: wp.get("id") or 0)

#UC2, UC4: Return project members for task assignment and utilization views
@app.get("/project/3/members")
def get_project_3_members() -> list[dict[str, Any]]:
	memberships = fetch_project_memberships(TARGET_PROJECT_ID)
	unique_members: dict[str, dict[str, Any]] = {}

	for membership in memberships:
		principal = membership.get("_links", {}).get("principal", {})
		if not isinstance(principal, dict):
			continue

		name = principal.get("title")
		href = principal.get("href", "")
		if not name or not href:
			continue

		member_id = href.rstrip("/").split("/")[-1]
		if not member_id:
			continue

		unique_members[member_id] = {
			"id": str(member_id),
			"name": str(name),
			"principalType": str(principal.get("type") or "Principal"),
		}

	return sorted(unique_members.values(), key=lambda member: member["name"].lower())

#UC1, UC3, UC4: Return network structure, scheduling data, and resource utilization for visualization
@app.get("/project/3/network")
def get_project_3_network() -> dict[str, Any]:
	return build_network_payload(TARGET_PROJECT_ID)
