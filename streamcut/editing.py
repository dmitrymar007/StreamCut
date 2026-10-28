"""Small reversible edit operations; queued export snapshots remain independent."""
from .domain import Clip


def apply_edit(project, operation):
    kind = operation.get("operation", "edit")
    if kind == "batch":
        inverses, selected = [], None
        for item in operation["items"]:
            inverse, focus = apply_edit(project, item)
            if inverse:
                inverses.append(inverse)
            selected = focus or selected
        return {"operation": "batch", "items": list(reversed(inverses))}, selected
    if kind == "insert":
        clip = Clip.model_validate(operation["clip"])
        project.clips.insert(min(operation["index"], len(project.clips)), clip)
        project.deleted_clips = [c for c in project.deleted_clips if c["id"] != clip.id]
        return {"operation": "remove", "id": clip.id}, clip.id
    identity = operation["id"]
    for index, clip in enumerate(project.clips):
        if clip.id != identity:
            continue
        if kind == "remove":
            project.clips.pop(index)
            project.deleted_clips = [c for c in project.deleted_clips if c["id"] != clip.id]+[clip.model_dump()]
            return {"operation": "insert", "index": index, "clip": clip.model_dump()}, None
        previous = clip.model_dump()
        project.clips[index] = Clip.model_validate(operation)
        return previous, identity
    return None, None
