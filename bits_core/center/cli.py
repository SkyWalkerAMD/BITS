"""Task management for a freshly deployed original-protocol OCRUN center."""
import argparse
import json
import sys

from . import VERSION
from . import safe, tasks
from .wire import Redis


def menu(redis, config):
    while True:
        print("\nOCRUN 任务管理：1 添加任务  2 删除任务  3 查询任务  Q 退出")
        operation = input("选择: ").strip().lower()
        if operation == "q":
            return
        try:
            host = tasks.name(input("主机编号（区分大小写）: ").strip())
            if operation == "1":
                batch = tasks.name(input("任务编号: ").strip())
                when = input("任务时间（留空自动生成）: ").strip() or None
                items = input("任务顺序，例如 stress=60 stress-ng=60: ").split()
                validated = tasks.task_list(items)
                print(json.dumps({"host": host, "id": batch, "time": when, "tasks": validated}, ensure_ascii=False, indent=2))
                if input("确认添加 [Y/N]: ").strip().upper() == "Y":
                    print(json.dumps(tasks.add(redis, host, batch, when, items, config["databases"]), ensure_ascii=False))
                    print("任务已写入；请在目标节点明确启动当前批次。")
            elif operation == "2":
                current = tasks.status(redis, host)
                print(json.dumps(current, ensure_ascii=False, indent=2))
                if current.get("id") and input("确认删除队列（保留结果文件）[Y/N]: ").strip().upper() == "Y":
                    print(json.dumps(tasks.delete(redis, host, current["id"]), ensure_ascii=False))
            elif operation == "3":
                print(json.dumps(tasks.status(redis, host), ensure_ascii=False, indent=2))
            else:
                print("请选择1、2、3或Q。")
        except (ValueError, OSError) as error:
            print("操作未完成: " + str(error), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="bits-center-runtime " + VERSION)
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("menu")
    sub.add_parser("check")
    connect = sub.add_parser('node-config')
    connect.add_argument('--node', required=True)
    connect.add_argument('--output', required=True)
    publish = sub.add_parser('publish')
    publish.add_argument('--file', required=True)
    publish.add_argument('--sha256', required=True)
    task = sub.add_parser("task").add_subparsers(dest="operation")
    add = task.add_parser("add")
    add.add_argument("--node", required=True)
    add.add_argument("--id", required=True)
    add.add_argument("--time")
    add.add_argument("--task", action="append", required=True)
    show = task.add_parser("status")
    show.add_argument("--node", required=True)
    remove = task.add_parser("delete")
    remove.add_argument("--node", required=True)
    remove.add_argument("--id", required=True)
    args = parser.parse_args()
    config = safe.load("/etc/bits/center/server.json")
    redis = Redis(password=config["password"])
    if args.action == "menu":
        menu(redis, config)
        return
    if args.action == "check":
        from .install import health, verify_managed
        verify_managed()
        result = health(config)
    elif args.action == "task" and args.operation == "add":
        result = tasks.add(redis, args.node, args.id, args.time, args.task, config["databases"])
    elif args.action == "task" and args.operation == "status":
        result = tasks.status(redis, args.node)
    elif args.action == "task" and args.operation == "delete":
        result = tasks.delete(redis, args.node, args.id)
    elif args.action == 'node-config':
        from .connection import export
        result = export(config, args.node, args.output)
    elif args.action == 'publish':
        from .publish import publish
        result = publish(config, args.file, args.sha256)
    else:
        parser.error("Choose check, menu, or task add/status/delete")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("bits-center-runtime: " + str(error), file=sys.stderr)
        sys.exit(1)
