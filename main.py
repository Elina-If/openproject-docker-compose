from fastapi import FastAPI

# FastAPI-Anwendung erstellen
app = FastAPI()

# Endpunkt für die Root-URL
@app.get("/")
def root():
    return {"message": "API läuft"}

# Test-Endpunkt
@app.get("/test")
def test():
    return {"status": "ok"}

# Endpunkt für die Arbeitspakete
import requests

url = "http://localhost:8080/api/v3/work_packages"

# Filter auf Projekt SWEBA_project_planning_tool (ID: 3) http://localhost:8080/api/v3/projects
@app.get("/tasks")
def get_tasks():
    url = "http://localhost:8080/api/v3/work_packages"

    filters = {
        "filters": '[{"project":{"operator":"=","values":["3"]}}]'
    }

    response = requests.get(
        url,
        params=filters,
        auth=("apikey", "opapi-99b19dcb0a1ad47170f7c50d2050a5b15d5fab000aa42fefbca7ad4d77040dd0")
    )

# Antwort formatieren, damit nur die relevanten Informationen zum Arbeitspaket zurück gegeben werden
    data = response.json()

    tasks = []

    for wp in data["_embedded"]["elements"]:
        tasks.append({
            "id": wp["id"],
            "name": wp["subject"],
            "start": wp.get("startDate"),
            "end": wp.get("dueDate"),
            "assignee": wp["_links"]["assignee"]["title"]
                if wp["_links"]["assignee"] else None
        })

    return tasks

def get_relations(task_id):
    url = f"http://localhost:8080/api/v3/work_packages/{task_id}/relations"

    response = requests.get(
        url,
        auth=("apikey", "opapi-99b19dcb0a1ad47170f7c50d2050a5b15d5fab000aa42fefbca7ad4d77040dd0")
    )

    return response.json()

class Task:
    def __init__(self, id, name, assignee):
        self.id = id
        self.name = name
        self.assignee = assignee
        self.predecessors = []
        self.successors = []

def parse_tasks(data):
    tasks = {}

    for wp in data["_embedded"]["elements"]:
        task = Task(
            id=wp["id"],
            name=wp["subject"],
            assignee=wp["_links"]["assignee"]["title"]
                if wp["_links"]["assignee"] else None
        )
        tasks[task.id] = task

    return tasks

def build_relations(tasks):
    for task_id in tasks:
        relations = get_relations(task_id)

        if "_embedded" in relations:
            for rel in relations["_embedded"]["elements"]:

                source_id = int(rel["_links"]["from"]["href"].split("/")[-1])
                target_id = int(rel["_links"]["to"]["href"].split("/")[-1])

                if source_id in tasks and target_id in tasks:
                    tasks[source_id].successors.append(tasks[target_id])
                    tasks[target_id].predecessors.append(tasks[source_id])

def to_network_json(tasks):
    nodes = []
    edges = []

    for task in tasks.values():
        nodes.append({
            "id": str(task.id),
            "label": task.name + " (" + str(task.assignee) + ")"
        })

        for succ in task.successors:
            edges.append({
                "source": str(task.id),
                "target": str(succ.id)
            })

    return {"nodes": nodes, "edges": edges}

def fetch_tasks_from_openproject():
    url = "http://localhost:8080/api/v3/work_packages"

    filters = {
        "filters": '[{"project":{"operator":"=","values":["3"]}}]'
    }

    response = requests.get(
        url,
        params=filters,
        auth=("apikey", "opapi-99b19dcb0a1ad47170f7c50d2050a5b15d5fab000aa42fefbca7ad4d77040dd0")
    )

    return response.json()


@app.get("/network")
def get_network():
    data = fetch_tasks_from_openproject()
    tasks = parse_tasks(data)
    build_relations(tasks)

    return to_network_json(tasks)