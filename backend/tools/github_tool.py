import json
import shlex
import subprocess
from typing import Any, Dict, List

from app.tooling import BaseTool


class GitHubTool(BaseTool):
    name = "github"
    display_name = "GitHub"
    description = "Inspect GitHub repositories, pull requests, issues, and workflow runs through gh."
    icon = "Github"
    requires_auth = False

    def get_functions(self) -> List[Dict[str, Any]]:
        return [
            self.function(
                "github_current_user",
                "Show the GitHub account currently authenticated with the gh CLI.",
                {},
            ),
            self.function(
                "github_search_issues",
                "Search GitHub issues and pull requests using GitHub search syntax.",
                {
                    "query": {"type": "string", "description": "GitHub issue search query."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                ["query"],
            ),
            self.function(
                "github_list_pull_requests",
                "List pull requests for a repository.",
                {
                    "repo": {"type": "string", "description": "Repository in owner/name form."},
                    "state": {"type": "string", "enum": ["open", "closed", "merged", "all"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                ["repo"],
            ),
            self.function(
                "github_view_pull_request",
                "View a pull request with files, reviews, comments, and status checks.",
                {
                    "repo": {"type": "string", "description": "Repository in owner/name form."},
                    "number": {"type": "integer", "minimum": 1},
                },
                ["repo", "number"],
            ),
            self.function(
                "github_view_issue",
                "View an issue with labels, assignees, comments, and body.",
                {
                    "repo": {"type": "string", "description": "Repository in owner/name form."},
                    "number": {"type": "integer", "minimum": 1},
                },
                ["repo", "number"],
            ),
            self.function(
                "github_list_workflow_runs",
                "List recent GitHub Actions workflow runs for a repository.",
                {
                    "repo": {"type": "string", "description": "Repository in owner/name form."},
                    "branch": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["queued", "completed", "in_progress", "requested", "waiting", "pending", ""],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                ["repo"],
            ),
            self.function(
                "github_view_workflow_run",
                "View a GitHub Actions workflow run summary.",
                {
                    "repo": {"type": "string", "description": "Repository in owner/name form."},
                    "run_id": {"type": "integer", "minimum": 1},
                },
                ["repo", "run_id"],
            ),
        ]

    def _run(self, args: List[str]) -> str:
        completed = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return f"GitHub tool error: {detail or 'gh command failed.'}"
        text = completed.stdout.strip()
        if not text:
            return "No GitHub results."
        try:
            return json.dumps(json.loads(text), indent=2)
        except json.JSONDecodeError:
            return text

    def _limit(self, parameters: Dict[str, Any], default: int = 20) -> str:
        value = int(parameters.get("limit") or default)
        return str(max(1, min(value, 50)))

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        try:
            if function_name == "github_current_user":
                return self._run(["api", "user", "--jq", "{login: .login, name: .name, url: .html_url}"])

            if function_name == "github_search_issues":
                query_terms = shlex.split(str(parameters["query"]))
                return self._run(
                    [
                        "search",
                        "issues",
                        *query_terms,
                        "--limit",
                        self._limit(parameters),
                        "--json",
                        "repository,number,title,state,author,updatedAt,url,isPullRequest",
                    ]
                )

            if function_name == "github_list_pull_requests":
                return self._run(
                    [
                        "pr",
                        "list",
                        "--repo",
                        str(parameters["repo"]),
                        "--state",
                        str(parameters.get("state") or "open"),
                        "--limit",
                        self._limit(parameters),
                        "--json",
                        "number,title,state,author,updatedAt,url,headRefName,baseRefName,isDraft",
                    ]
                )

            if function_name == "github_view_pull_request":
                return self._run(
                    [
                        "pr",
                        "view",
                        str(parameters["number"]),
                        "--repo",
                        str(parameters["repo"]),
                        "--json",
                        "number,title,state,author,body,files,commits,comments,reviews,statusCheckRollup,url,headRefName,baseRefName,isDraft",
                    ]
                )

            if function_name == "github_view_issue":
                return self._run(
                    [
                        "issue",
                        "view",
                        str(parameters["number"]),
                        "--repo",
                        str(parameters["repo"]),
                        "--json",
                        "number,title,state,author,body,comments,labels,assignees,updatedAt,url",
                    ]
                )

            if function_name == "github_list_workflow_runs":
                args = [
                    "run",
                    "list",
                    "--repo",
                    str(parameters["repo"]),
                    "--limit",
                    self._limit(parameters),
                    "--json",
                    "databaseId,displayTitle,headBranch,status,conclusion,event,createdAt,updatedAt,url,workflowName",
                ]
                if parameters.get("branch"):
                    args.extend(["--branch", str(parameters["branch"])])
                if parameters.get("status"):
                    args.extend(["--status", str(parameters["status"])])
                return self._run(args)

            if function_name == "github_view_workflow_run":
                return self._run(
                    [
                        "run",
                        "view",
                        str(parameters["run_id"]),
                        "--repo",
                        str(parameters["repo"]),
                        "--json",
                        "databaseId,displayTitle,headBranch,status,conclusion,event,createdAt,updatedAt,url,workflowName,jobs",
                    ]
                )
        except Exception as exc:
            return f"GitHub tool error: {exc}"
        return f"Unknown GitHub function: {function_name}"
