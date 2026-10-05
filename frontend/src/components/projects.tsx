"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition, useEffect, useState, type ComponentProps, type FormEvent } from "react";
import { api } from "@/lib/api";
import type { Project, Task } from "@/lib/types";
import { EmptyState, ErrorState, ProgressBar, Skeleton, StatusBadge } from "./ui";
import { ProjectPeople } from "./relationships";
import { ProjectForm, type ProjectFormValues } from "./forms";

type ProjectDraft = NonNullable<ComponentProps<typeof ProjectForm>["defaultValues"]>;

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]); const [tasks, setTasks] = useState<Task[]>([]); const [query, setQuery] = useState(""); const [error, setError] = useState<string | null>(null); const [jiraConnectError, setJiraConnectError] = useState<string | null>(null); const [githubConnectError, setGitHubConnectError] = useState<string | null>(null); const [connectedGitHubInstallationId, setConnectedGitHubInstallationId] = useState<string>(); const [loading, setLoading] = useState(true); const [showCreate, setShowCreate] = useState(false); const [projectDraft, setProjectDraft] = useState<ProjectDraft>(); const [autoLoadJiraProjects, setAutoLoadJiraProjects] = useState(false); const [autoLoadGitHubRepositories, setAutoLoadGitHubRepositories] = useState(false); const router = useRouter();
  useEffect(() => { void (async () => { try { const result = await api.projects(); setProjects(result.items); const taskPages = await Promise.all(result.items.map((project) => api.projectTasks(project.project_id))); setTasks(taskPages.flatMap((page) => page.items)); } catch (caught) { setError(caught instanceof Error ? caught.message : "The API is unavailable."); } finally { setLoading(false); } })(); }, []);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const jiraStatus = params.get("jira_status");
    const jiraError = params.get("jira_error");
    const githubStatus = params.get("github_status");
    const githubError = params.get("github_error");
    const githubInstallationId = params.get("github_installation_id");
    if (!jiraStatus && !jiraError && !githubStatus && !githubError && !githubInstallationId) return;

    startTransition(() => {
      setShowCreate(true);
      setAutoLoadJiraProjects(jiraStatus === "connected");
      setJiraConnectError(jiraError);
      setAutoLoadGitHubRepositories(githubStatus === "connected");
      setGitHubConnectError(githubError);
      if (githubInstallationId) setConnectedGitHubInstallationId(githubInstallationId);
    });
    const savedDraft = sessionStorage.getItem("astra_project_create_draft");
    if (savedDraft) {
      try {
        const parsedDraft = JSON.parse(savedDraft) as ProjectDraft;
        const draft = {
          ...parsedDraft,
          github_installation_id: githubInstallationId ?? parsedDraft.github_installation_id ?? "",
        };
        startTransition(() => setProjectDraft(draft));
      } catch {
        sessionStorage.removeItem("astra_project_create_draft");
      }
      sessionStorage.removeItem("astra_project_create_draft");
    } else if (githubInstallationId) {
      startTransition(() => setProjectDraft({ github_installation_id: githubInstallationId }));
    }

    params.delete("jira_status");
    params.delete("jira_error");
    params.delete("github_status");
    params.delete("github_error");
    params.delete("github_installation_id");
    const remainingQuery = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${remainingQuery ? `?${remainingQuery}` : ""}`);
  }, []);
  const filtered = projects.filter((project) => `${project.name} ${project.project_id}`.toLowerCase().includes(query.toLowerCase()));
  async function createProject(value: ProjectFormValues) { try { const project = await api.createProject({ project_id: value.project_id, name: value.name, description: value.description || null, status: value.status, owner_employee_id: value.owner_employee_id || null, department: value.department || null, github: value.github_repository_id ? { provider: "github", installation_id: value.github_installation_id, repository_id: value.github_repository_id } : null, jira: value.jira_project_id && value.jira_project_key ? { provider: "jira", project_id: value.jira_project_id, project_key: value.jira_project_key } : null }); sessionStorage.removeItem("astra_project_create_draft"); setProjects((current) => [...current, project]); setShowCreate(false); router.push(`/dashboard/projects/${project.project_id}`); } catch (caught) { setError(caught instanceof Error ? caught.message : "Project could not be created."); } }
  return (
    <div className="page">
      <div className="page-heading">
        <div><p className="eyebrow">Workspace / Projects</p><h1>Projects</h1><p>See the health, allocation, and current movement across every project.</p></div>
        <button className="button primary" onClick={() => setShowCreate((current) => !current)}>Create project</button>
      </div>
      {showCreate && <>
        {jiraConnectError && <small className="field-error">Jira could not be connected{jiraConnectError.startsWith("callback-") ? ` (backend HTTP ${jiraConnectError.slice("callback-".length)})` : ""}. Your project details are still here; you can retry.</small>}
        {githubConnectError && <small className="field-error">GitHub connection failed. Your project details are still here; you can retry.</small>}
        <ProjectForm defaultValues={projectDraft} autoLoadJiraProjects={autoLoadJiraProjects} autoLoadGitHubRepositories={autoLoadGitHubRepositories} onSubmit={createProject} submitLabel="Create project" />
        <ProjectForm defaultValues={projectDraft} autoLoadJiraProjects={autoLoadJiraProjects} autoLoadGitHubRepositories={autoLoadGitHubRepositories} connectedGitHubInstallationId={connectedGitHubInstallationId} onSubmit={createProject} submitLabel="Create project" />
      </>}
      <div className="toolbar"><input aria-label="Search projects" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search projects..." /></div>
      {error ? <ErrorState message={error} /> : <div className="panel">{loading ? <div className="workload"><Skeleton /><Skeleton /><Skeleton /></div> : filtered.length === 0 ? <EmptyState title={projects.length ? "No matching projects" : "No projects yet"}>{projects.length ? "Try another search term." : "Create your first project to start tracking work."}</EmptyState> : filtered.map((project) => { const projectTasks = tasks.filter((task) => task.project_id === project.project_id); const progress = projectTasks.length ? Math.round(projectTasks.reduce((sum, task) => sum + task.progress, 0) / projectTasks.length) : 0; return <div className="project-row" key={project.project_id}><Link className="project-name" href={`/dashboard/projects/${project.project_id}/tasks`}>{project.name}<span className="project-id">{project.project_id}</span></Link><div className="progress-cell"><span className="progress-value">{progress}% complete</span><ProgressBar value={progress} /></div><span>{projectTasks.length} tasks</span><StatusBadge value={project.status} /><span>{project.department ?? "Unassigned"}</span></div>; })}</div>}
    </div>
  );
}

export function ProjectDetail({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null); const [tasks, setTasks] = useState<Task[]>([]); const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(true); const [showEdit, setShowEdit] = useState(false);
  useEffect(() => { void (async () => { try { const [projectResult, taskResult] = await Promise.all([api.project(projectId), api.projectTasks(projectId)]); setProject(projectResult); setTasks(taskResult.items); } catch (caught) { setError(caught instanceof Error ? caught.message : "Project not found."); } finally { setLoading(false); } })(); }, [projectId]);
  if (loading) return <div className="page"><Skeleton className="hero-skeleton" /><div className="panel"><div className="workload"><Skeleton /><Skeleton /><Skeleton /></div></div></div>;
  if (error || !project) return <div className="page"><ErrorState message={error ?? "Project not found."} /></div>;
  const progress = tasks.length ? Math.round(tasks.reduce((sum, task) => sum + task.progress, 0) / tasks.length) : 0;
  async function updateProject(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); try { const updated = await api.updateProject(projectId, { name: String(form.get("name")), description: String(form.get("description") ?? "") || null, status: String(form.get("status")) }); setProject(updated); setShowEdit(false); } catch (caught) { setError(caught instanceof Error ? caught.message : "Project could not be updated."); } }
  return <div className="page"><Link className="back-link" href="/dashboard/projects">← All projects</Link><div className="page-heading detail-heading"><div><div className="title-row"><h1>{project.name}</h1><StatusBadge value={project.status} /></div><p>{project.description ?? "No project description has been added."}</p><small className="project-id">{project.project_id} · {project.department ?? "No department"}</small></div><button className="button secondary" onClick={() => setShowEdit((current) => !current)}>Edit project</button></div>{showEdit && <form className="form-panel" onSubmit={updateProject}><input name="name" required defaultValue={project.name} /><select name="status" defaultValue={project.status}><option>PLANNED</option><option>ACTIVE</option><option>IN_PROGRESS</option><option>ON_HOLD</option><option>COMPLETED</option><option>ARCHIVED</option></select><textarea name="description" defaultValue={project.description ?? ""} /><button className="button primary" type="submit">Save changes</button></form>}<div className="detail-metrics"><div><span>Progress</span><strong>{progress}%</strong><ProgressBar value={progress} /></div><div><span>Tasks</span><strong>{tasks.length}</strong><small>{tasks.filter((task) => task.status === "BLOCKED").length} blocked</small></div><div><span>Integrations</span><strong>{[project.github, project.jira].filter(Boolean).length}</strong><small>{project.github ? "GitHub linked" : "No GitHub link"}</small></div></div>{project.jira && <div className="panel"><div className="section-heading"><h2>Jira</h2></div><p>Connected to Jira project <strong>{project.jira.project_key}</strong>.</p>{project.jira.project_id && <small>Project ID: {project.jira.project_id}</small>}</div>}<div className="section-heading"><h2>Project tasks</h2><Link href={`/dashboard/projects/${projectId}/tasks`}>Open task view →</Link></div><div className="panel">{tasks.length ? tasks.map((task) => <div className="task-row" key={task.task_id}><Link href={`/dashboard/tasks/${projectId}/${task.task_id}`}>{task.title}<small>{task.task_id}</small></Link><StatusBadge value={task.status} /><span>{task.progress}%</span><span className={`priority ${task.priority.toLowerCase()}`}>{task.priority}</span></div>) : <EmptyState title="No tasks in this project">Tasks added through the management API will appear here.</EmptyState>}</div><div className="section-heading"><h2>Project people</h2></div><ProjectPeople projectId={projectId} /><div className="section-heading"><h2>Intelligence surfaces</h2></div><div className="split-grid"><div className="panel"><div className="panel-title">Activity</div><EmptyState title="Activity is not connected">GitHub, Jira, and desktop-agent observations will appear when those APIs are available.</EmptyState></div><div className="panel"><div className="panel-title">Evidence</div><EmptyState title="No evidence collected">Evidence will explain why Astra believes a task is in its current state.</EmptyState></div></div></div>;
}