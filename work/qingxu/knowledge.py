"""Project knowledge categories and explicit creation of missing templates."""
from pathlib import Path

from relay import KB_FILES
from sync import publish_new, safe_path


TEMPLATE_SECTIONS = {
    "REQUIREMENTS": ("项目目标", "需求与范围", "验收条件"),
    "PLAN": ("阶段与步骤", "依赖", "下一步"),
    "NOW": ("已验收进度", "证据", "剩余事项"),
    "DECISIONS": ("关键决策", "取舍与原因", "长期阻塞"),
    "LESSONS": ("可复用流程", "经验与踩坑", "验证方法"),
    "RESEARCH": ("调研结论", "资料入口", "适用范围"),
    "BUGS": ("问题现象", "根因与修复", "验证与版本"),
}


def validate_categories(categories):
    if not isinstance(categories, (list, tuple)):
        raise ValueError("知识分类须为分类列表。")
    if any(not isinstance(kind, str) or kind not in KB_FILES for kind in categories):
        raise ValueError("知识分类包含未知项目。")
    if len(set(categories)) != len(categories):
        raise ValueError("知识分类不能重复。")
    return [kind for kind in KB_FILES if kind in categories]


def project_folder(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("请选择当前项目的知识文件夹。")
    folder = safe_path(Path(value).expanduser())
    if not folder.is_dir():
        raise ValueError("项目知识文件夹不存在或不是文件夹。")
    marker = folder / ".obsidian"
    if folder.parent == folder or marker.exists() or marker.is_symlink() or folder.name == "03-项目记忆":
        raise ValueError("请选择项目文件夹，不能选择 Vault 根目录或项目记忆总目录。")
    return folder


def create_templates(directory, categories):
    """Create selected missing files atomically without replacing existing pages."""

    folder = project_folder(directory)
    kinds = validate_categories(categories)
    if not kinds:
        raise ValueError("请先勾选至少一个知识分类。")
    targets = [(kind, safe_path(folder / KB_FILES[kind])) for kind in kinds]
    for _kind, target in targets:
        if target.exists() and not target.is_file():
            raise ValueError("目标页不是普通文件：" + str(target))
    created, existing = [], []
    for kind, target in targets:
        if target.exists():
            existing.append(str(target))
            continue
        title = KB_FILES[kind].removesuffix(".md").split("-", 1)[-1]
        content = ("# " + title + "\n\n" +
                   "\n\n".join("## " + section for section in TEMPLATE_SECTIONS[kind]) + "\n").encode("utf-8")
        try:
            publish_new(target, content)
        except (OSError, ValueError) as exc:
            done = "、".join(created) if created else "无"
            raise ValueError("模板创建未完成。已完成：" + done + "。请检查目标页 " +
                             str(target) + "。原因：" + str(exc)) from exc
        created.append(str(target))
    return {"created": created, "existing": existing}
