"""Tree node CRUD and recursive tree building."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .audit_service import log_action
from .models import ImageLibrary, TreeNode
from .path_service import delete_directory, ensure_node_directory, move_directory
from .path_slugs import unique_slug

logger = logging.getLogger(__name__)


def _sibling_slugs(db: Session, parent_id: Optional[int]) -> set:
    q = db.query(TreeNode).filter(TreeNode.parent_id == parent_id, TreeNode.is_active == True)  # noqa: E712
    return {n.slug for n in q.all() if n.slug}


def _sibling_names(db: Session, parent_id: Optional[int], exclude_id: Optional[int] = None) -> set:
    q = db.query(TreeNode).filter(TreeNode.parent_id == parent_id, TreeNode.is_active == True)  # noqa: E712
    if exclude_id:
        q = q.filter(TreeNode.id != exclude_id)
    return {n.node_name.strip().lower() for n in q.all()}


def _node_to_dict(node: TreeNode, *, image_count: int = 0, children: Optional[list] = None) -> dict:
    return {
        "id": node.id,
        "parentId": node.parent_id,
        "name": node.node_name,
        "nodeName": node.node_name,
        "nodeType": node.node_type,
        "nodeLevel": node.node_level,
        "nodePath": node.node_path,
        "slug": node.slug,
        "physicalPath": node.physical_path,
        "imageCount": image_count,
        "children": children or [],
    }


def build_tree(db: Session, parent_id: Optional[int] = None) -> List[dict]:
    nodes = (
        db.query(TreeNode)
        .filter(TreeNode.parent_id == parent_id, TreeNode.is_active == True)  # noqa: E712
        .order_by(TreeNode.node_name)
        .all()
    )
    result = []
    for node in nodes:
        children = build_tree(db, node.id)
        img_count = db.query(ImageLibrary).filter(ImageLibrary.node_id == node.id).count()
        child_img = sum(c.get("imageCount", 0) for c in children)
        result.append(_node_to_dict(node, image_count=img_count + child_img, children=children))
    return result


def get_node_or_404(db: Session, node_id: int) -> TreeNode:
    node = db.query(TreeNode).filter(TreeNode.id == node_id, TreeNode.is_active == True).first()  # noqa: E712
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


def create_node(
    db: Session,
    *,
    parent_id: Optional[int],
    node_name: str,
    node_type: str,
    created_by: str,
) -> TreeNode:
    name = node_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="node_name is required")
    if name.lower() in _sibling_names(db, parent_id):
        raise HTTPException(status_code=400, detail="Duplicate node name under same parent")

    parent = None
    level = 0
    parent_physical = ""
    parent_display = ""
    if parent_id is not None:
        parent = get_node_or_404(db, parent_id)
        level = parent.node_level + 1
        parent_physical = parent.physical_path or ""
        parent_display = parent.node_path or parent.node_name

    slug = unique_slug(name, _sibling_slugs(db, parent_id))
    physical_path = f"{parent_physical}/{slug}".strip("/") if parent_physical else slug
    node_path = f"{parent_display}/{name}".strip("/") if parent_display else name

    node = TreeNode(
        parent_id=parent_id,
        node_name=name,
        node_type=node_type or "Folder",
        node_level=level,
        node_path=node_path,
        slug=slug,
        physical_path=physical_path,
        created_by=created_by or "",
    )
    db.add(node)
    db.flush()
    ensure_node_directory(physical_path)
    log_action(db, "create", node_id=node.id, new_value={"name": name, "path": node_path}, action_by=created_by)
    db.commit()
    db.refresh(node)
    logger.info("Created tree node %s (%s)", node_path, physical_path)
    return node


def rename_node(
    db: Session,
    node_id: int,
    new_name: str,
    *,
    action_by: str,
    rename_physical: bool = False,
) -> TreeNode:
    node = get_node_or_404(db, node_id)
    name = new_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="node_name is required")
    if name.lower() in _sibling_names(db, node.parent_id, exclude_id=node_id):
        raise HTTPException(status_code=400, detail="Duplicate node name under same parent")

    old = {"name": node.node_name, "path": node.node_path}
    node.node_name = name

    if node.parent_id:
        parent = get_node_or_404(db, node.parent_id)
        node.node_path = f"{parent.node_path}/{name}"
    else:
        node.node_path = name

    if rename_physical:
        old_physical = node.physical_path
        parent_physical = ""
        if node.parent_id:
            parent = get_node_or_404(db, node.parent_id)
            parent_physical = parent.physical_path or ""
        new_slug = unique_slug(name, _sibling_slugs(db, node.parent_id) - {node.slug})
        new_physical = f"{parent_physical}/{new_slug}".strip("/") if parent_physical else new_slug
        if old_physical != new_physical:
            move_directory(old_physical, new_physical)
            node.slug = new_slug
            node.physical_path = new_physical
            _update_descendant_paths(db, node)

    log_action(db, "rename", node_id=node.id, old_value=old, new_value={"name": name, "path": node.node_path}, action_by=action_by)
    db.commit()
    db.refresh(node)
    return node


def _update_descendant_paths(db: Session, parent: TreeNode) -> None:
    children = db.query(TreeNode).filter(TreeNode.parent_id == parent.id, TreeNode.is_active == True).all()  # noqa: E712
    for child in children:
        child.node_path = f"{parent.node_path}/{child.node_name}"
        child.physical_path = f"{parent.physical_path}/{child.slug}".strip("/")
        ensure_node_directory(child.physical_path)
        _update_descendant_paths(db, child)


def move_node(
    db: Session,
    node_id: int,
    new_parent_id: Optional[int],
    *,
    action_by: str,
) -> TreeNode:
    node = get_node_or_404(db, node_id)
    if new_parent_id == node.id:
        raise HTTPException(status_code=400, detail="Cannot move node under itself")

    if new_parent_id is not None:
        new_parent = get_node_or_404(db, new_parent_id)
        # prevent cycle
        cursor = new_parent
        while cursor.parent_id is not None:
            if cursor.parent_id == node.id:
                raise HTTPException(status_code=400, detail="Cannot move node under its descendant")
            cursor = get_node_or_404(db, cursor.parent_id)

        if node.node_name.lower() in _sibling_names(db, new_parent_id, exclude_id=node_id):
            raise HTTPException(status_code=400, detail="Duplicate node name under target parent")

    old = {"parentId": node.parent_id, "path": node.node_path, "physical": node.physical_path}
    old_physical = node.physical_path

    node.parent_id = new_parent_id
    if new_parent_id is None:
        node.node_level = 0
        node.node_path = node.node_name
        new_physical = node.slug
    else:
        new_parent = get_node_or_404(db, new_parent_id)
        node.node_level = new_parent.node_level + 1
        node.node_path = f"{new_parent.node_path}/{node.node_name}"
        new_physical = f"{new_parent.physical_path}/{node.slug}".strip("/")

    if old_physical != new_physical:
        move_directory(old_physical, new_physical)
        node.physical_path = new_physical
        _update_descendant_paths(db, node)

    log_action(db, "move", node_id=node.id, old_value=old, new_value={"parentId": new_parent_id, "path": node.node_path}, action_by=action_by)
    db.commit()
    db.refresh(node)
    return node


def _descendant_ids(db: Session, node_id: int) -> List[int]:
    ids = [node_id]
    children = db.query(TreeNode.id).filter(TreeNode.parent_id == node_id, TreeNode.is_active == True).all()  # noqa: E712
    for (cid,) in children:
        ids.extend(_descendant_ids(db, cid))
    return ids


def delete_node(
    db: Session,
    node_id: int,
    *,
    delete_files: bool,
    action_by: str,
) -> dict:
    node = get_node_or_404(db, node_id)
    ids = _descendant_ids(db, node_id)
    img_count = db.query(ImageLibrary).filter(ImageLibrary.node_id.in_(ids)).count()
    if img_count > 0 and not delete_files:
        raise HTTPException(status_code=400, detail="Node has images; set delete_files=true to remove")

    old = {"path": node.node_path, "physical": node.physical_path}
    if delete_files:
        for nid in reversed(ids):
            n = db.query(TreeNode).filter(TreeNode.id == nid).first()
            if n and n.physical_path:
                delete_directory(n.physical_path)
            db.query(ImageLibrary).filter(ImageLibrary.node_id == nid).delete()
            if n:
                n.is_active = False
    else:
        child_count = len(ids) - 1
        if child_count > 0:
            raise HTTPException(status_code=400, detail="Node has child nodes; set delete_files=true or remove children first")
        if img_count > 0:
            raise HTTPException(status_code=400, detail="Node has images")
        node.is_active = False

    log_action(db, "delete", node_id=node_id, old_value=old, new_value={"delete_files": delete_files}, action_by=action_by)
    db.commit()
    return {"ok": True, "deletedIds": ids}
