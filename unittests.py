from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class Task:
    id: str
    duration: int
    predecessors: list["Task"] = field(default_factory=list)
    early_start: int = 0
    early_end: int = 0
    late_start: int = 0
    late_end: int = 0
    buffer: int = 0


def is_valid_assignment(assignment: list[tuple[str, str, int, int]]) -> bool:
    for index, (_, resource_a, start_a, end_a) in enumerate(assignment):
        for _, resource_b, start_b, end_b in assignment[index + 1 :]:
            if resource_a != resource_b:
                continue
            if start_a <= end_b and start_b <= end_a:
                return False
    return True


def calculate_schedule(tasks: list[Task]) -> None:
    pending = list(tasks)
    ordered: list[Task] = []

    while pending:
        progressed = False
        for task in pending[:]:
            if all(predecessor in ordered for predecessor in task.predecessors):
                task.early_start = max((predecessor.early_end for predecessor in task.predecessors), default=0)
                task.early_end = task.early_start + task.duration
                ordered.append(task)
                pending.remove(task)
                progressed = True
        if not progressed:
            raise ValueError("Task dependencies contain a cycle.")

    project_end = max((task.early_end for task in ordered), default=0)

    for task in reversed(ordered):
        successors = [candidate for candidate in ordered if task in candidate.predecessors]
        task.late_end = min((successor.late_start for successor in successors), default=project_end)
        task.late_start = task.late_end - task.duration
        task.buffer = task.late_start - task.early_start


def create_sample_project() -> list[Task]:
    p1 = Task("P1", 3)
    p2 = Task("P2", 2)
    p3 = Task("P3", 4, predecessors=[p1])
    p4 = Task("P4", 1, predecessors=[p2])
    p5 = Task("P5", 2, predecessors=[p3, p4])

    return [p1, p2, p3, p4, p5]


def calculate_critical_path(tasks: list[Task]) -> list[str]:
    calculate_schedule(tasks)
    critical_tasks = [task for task in tasks if task.buffer == 0]
    critical_tasks.sort(key=lambda task: (task.early_start, task.id))
    return [task.id for task in critical_tasks]


def calculate_utilization(assignments: list[tuple[str, int, int]]) -> dict[str, int]:
    utilization: dict[str, int] = {}
    for resource, start, end in assignments:
        utilization[resource] = utilization.get(resource, 0) + (end - start)
    return utilization


def calculate_idle_time(assignments: list[tuple[str, int, int]]) -> int:
    if len(assignments) < 2:
        return 0

    ordered = sorted(assignments, key=lambda item: (item[1], item[2]))
    idle_time = 0
    for (_, _, previous_end), (_, next_start, _) in zip(ordered, ordered[1:]):
        idle_time += max(0, next_start - previous_end)
    return idle_time


@pytest.fixture
def sample_project() -> list[Task]:
	return create_sample_project()


#UC1: Correct project graph creation.
def test_create_project_graph() -> None:
    tasks = [
        Task("P1", 3),
        Task("P2", 2)
    ]
    tasks[1].predecessors.append(tasks[0])

    assert tasks[1].predecessors[0].id == "P1"


#UC1: Dependencies are stored correctly.
def test_task_dependencies() -> None:
    task1 = Task("P1", 3)
    task2 = Task("P2", 2)

    task2.predecessors.append(task1)

    assert len(task2.predecessors) == 1


#UC2: Prevent double assignment of the same resource.
@pytest.mark.parametrize(
	("assignment", "expected_validity"),
	[
		([
			("P1", "A1", 0, 5),
			("P2", "A1", 3, 7),
		], False),
		([
			("P1", "A1", 0, 3),
			("P2", "A1", 4, 6),
		], True),
	],
)
def test_assignment_validity(
	assignment: list[tuple[str, str, int, int]],
	expected_validity: bool,
) -> None:
	assert is_valid_assignment(assignment) is expected_validity


#UC3: Calculate the earliest start correctly.
def test_early_start() -> None:
    p1 = Task("P1", 3)
    p2 = Task("P2", 2)

    p2.predecessors.append(p1)

    calculate_schedule([p1, p2])

    assert p2.early_start == 3


#UC3: Detect the critical path correctly.
def test_critical_path(sample_project: list[Task]) -> None:
    critical_path = calculate_critical_path(sample_project)

    assert ["P1", "P3", "P5"] == critical_path


#UC3: Compute buffer times.
def test_buffer_time(sample_project: list[Task]) -> None:
    calculate_schedule(sample_project)

    assert sample_project[1].buffer >= 0


#UC4: Aggregate resource utilization correctly.
@pytest.mark.parametrize(
	("assignments", "expected_utilization", "expected_idle_time"),
	[
		([
			("A1", 0, 3),
			("A1", 4, 6),
		], 5, 1),
		([
			("A1", 0, 3),
			("A1", 10, 12),
		], 5, 7),
	],
)
def test_resource_metrics(
	assignments: list[tuple[str, int, int]],
	expected_utilization: int,
	expected_idle_time: int,
) -> None:
	assert calculate_utilization(assignments)["A1"] == expected_utilization
	assert calculate_idle_time(assignments) == expected_idle_time