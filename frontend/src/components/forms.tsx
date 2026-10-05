"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Employee, Team } from "@/lib/types";

const employeeSchema = z.object({ employee_id: z.string().min(1, "Employee ID is required"), name: z.string().min(1, "Name is required"), email: z.string().email("Enter a valid email"), role: z.string().min(1, "Role is required"), department: z.string().min(1, "Department is required"), manager_id: z.string(), employment_status: z.enum(["ACTIVE", "INACTIVE", "ON_LEAVE"]), access_role: z.enum(["MANAGER", "EMPLOYEE"]), cognito_user_id: z.string(), github_user_id: z.string().default(""), github_login: z.string().default("") });
const projectSchema = z.object({ project_id: z.string().min(1, "Project ID is required"), name: z.string().min(1, "Name is required"), description: z.string().default(""), owner_employee_id: z.string().default(""), department: z.string().default(""), status: z.enum(["PLANNED", "ACTIVE", "IN_PROGRESS", "ON_HOLD", "COMPLETED", "ARCHIVED"]), github_installation_id: z.string().default(""), github_repository_id: z.string().default(""), jira_project_id: z.string().optional().default(""), jira_project_key: z.string().optional().default("") });
const teamSchema = z.object({ team_id: z.string().min(1, "Team ID is required"), name: z.string().min(1, "Name is required"), description: z.string(), team_lead_id: z.string(), status: z.enum(["ACTIVE", "INACTIVE"]) });
const membershipSchema = z.object({ employee_id: z.string().min(1, "Employee is required"), role: z.string().min(1, "Role is required"), allocation_percentage: z.coerce.number().min(0).max(100), team_id: z.string(), status: z.enum(["ACTIVE", "INACTIVE"]) });
const taskSchema = z.object({ task_id: z.string().min(1, "Task ID is required"), title: z.string().min(1, "Title is required"), description: z.string(), jira_issue_key: z.string(), assignee_id: z.string(), team_id: z.string(), priority: z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]), estimated_hours: z.coerce.number().min(0).nullable(), actual_hours: z.coerce.number().min(0), acceptance_criteria: z.string() });

type FormProps<T> = { defaultValues?: Partial<T>; onSubmit: (value: T) => void | Promise<void>; submitting?: boolean; submitLabel?: string };
export type EmployeeFormValues = z.infer<typeof employeeSchema>;
export type ProjectFormValues = z.infer<typeof projectSchema>;
function FieldError({ message }: { message?: string }) { return message ? <small className="field-error">{message}</small> : null; }
function FormActions({ submitting, submitLabel }: { submitting?: boolean; submitLabel: string }) { return <button className="button primary" type="submit" disabled={submitting}>{submitting ? "Saving..." : submitLabel}</button>; }

export function EmployeeForm({ defaultValues, onSubmit, submitting, submitLabel = "Save employee" }: FormProps<z.infer<typeof employeeSchema>>) {
  const { register, handleSubmit, setValue, formState: { errors } } = useForm<z.input<typeof employeeSchema>, unknown, z.output<typeof employeeSchema>>({ resolver: zodResolver(employeeSchema), defaultValues: { employee_id: "", manager_id: "", employment_status: "ACTIVE", access_role: "EMPLOYEE", cognito_user_id: "", github_user_id: "", github_login: "", ...defaultValues } });
  const employeeId = defaultValues?.employee_id;
  const needsGitHubIdentity = Boolean(employeeId && defaultValues?.github_user_id === undefined && defaultValues?.github_login === undefined);
  useEffect(() => {
    if (!employeeId || !needsGitHubIdentity) return;
    let active = true;
    void api.employee(employeeId).then((employee) => {
      if (!active) return;
      setValue("github_user_id", employee.github?.user_id ?? "");
      setValue("github_login", employee.github?.login ?? "");
    }).catch(() => {});
    return () => { active = false; };
  }, [employeeId, needsGitHubIdentity, setValue]);
  return (
    <form className="form-panel" onSubmit={handleSubmit(onSubmit)}>
      <label>Employee ID<input {...register("employee_id")} readOnly={Boolean(defaultValues?.employee_id)} /> <FieldError message={errors.employee_id?.message} /></label>
      <label>Name<input {...register("name")} /> <FieldError message={errors.name?.message} /></label>
      <label>Email<input type="email" {...register("email")} /> <FieldError message={errors.email?.message} /></label>
      <label>Job role<input {...register("role")} /> <FieldError message={errors.role?.message} /></label>
      <label>Department<input {...register("department")} /> <FieldError message={errors.department?.message} /></label>
      <label>Manager ID<input {...register("manager_id")} /></label>
      {!defaultValues?.employee_id && <><label>Access role<select {...register("access_role")}><option value="EMPLOYEE">Employee</option><option value="MANAGER">Manager</option></select></label><label>Cognito user ID (sub)<input {...register("cognito_user_id")} /></label></>}
      <p className="form-context">GitHub identity (numeric user ID is used for attribution)</p>
      <label>GitHub user ID<input inputMode="numeric" {...register("github_user_id")} /></label>
      <label>GitHub login<input {...register("github_login")} /></label>
      <label>Employment status<select {...register("employment_status")}><option>ACTIVE</option><option>INACTIVE</option><option>ON_LEAVE</option></select></label>
      <FormActions submitting={submitting} submitLabel={submitLabel} />
    </form>
  );
}

export function ProjectForm({ defaultValues, onSubmit, submitting, submitLabel = "Save project", autoLoadJiraProjects = false, autoLoadGitHubRepositories = false, connectedGitHubInstallationId, githubReturnTo = "/dashboard/projects", showJiraIntegration = true }: FormProps<z.infer<typeof projectSchema>> & { autoLoadJiraProjects?: boolean; autoLoadGitHubRepositories?: boolean; connectedGitHubInstallationId?: string; githubReturnTo?: string; showJiraIntegration?: boolean }) {
  const { register, handleSubmit, setValue, watch, getValues, formState: { errors } } = useForm<z.input<typeof projectSchema>, unknown, z.output<typeof projectSchema>>({ resolver: zodResolver(projectSchema), defaultValues: { project_id: "", description: "", owner_employee_id: "", department: "", status: "PLANNED", github_installation_id: "", github_repository_id: "", jira_project_id: "", jira_project_key: "", ...defaultValues } });
  const [jiraProjects, setJiraProjects] = useState<Array<{ id: string; key: string; name: string }>>([]);
  const [jiraStatus, setJiraStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [jiraError, setJiraError] = useState<string | null>(null);
  const [githubRepositories, setGitHubRepositories] = useState<Array<{ repository_id: string; organization: string; repository: string; full_name: string }>>([]);
  const [githubStatus, setGitHubStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [githubError, setGitHubError] = useState<string | null>(null);
  const selectedProjectId = watch("jira_project_id");

  useEffect(() => {
    const match = jiraProjects.find((project) => project.id === selectedProjectId);
    if (match) {
      setValue("jira_project_key", match.key, { shouldDirty: true });
    }
  }, [jiraProjects, selectedProjectId, setValue]);

  useEffect(() => {
    if (!autoLoadJiraProjects) return;
    setJiraStatus("loading");
    setJiraError(null);
    void api.jiraProjects()
      .then((result) => {
        setJiraProjects(result.projects);
        setJiraStatus(result.projects.length ? "ready" : "idle");
      })
      .catch((caught: unknown) => {
        setJiraStatus("error");
        setJiraError(caught instanceof Error ? caught.message : "Jira projects could not be loaded.");
      });
  }, [autoLoadJiraProjects]);

  useEffect(() => {
    if (connectedGitHubInstallationId) {
      setValue("github_installation_id", connectedGitHubInstallationId);
    }
    if (!autoLoadGitHubRepositories || !connectedGitHubInstallationId) return;
    const projectId = getValues("project_id");
    if (!projectId) {
      setGitHubStatus("error");
      setGitHubError("Enter the Astra project ID before loading repositories.");
      return;
    }
    setGitHubStatus("loading");
    void api.githubRepositories(connectedGitHubInstallationId, projectId)
      .then((result) => {
        setGitHubRepositories(result.repositories);
        setGitHubStatus("ready");
      })
      .catch((caught: unknown) => {
        setGitHubStatus("error");
        setGitHubError(caught instanceof Error ? caught.message : "GitHub repositories could not be loaded.");
      });
  }, [autoLoadGitHubRepositories, connectedGitHubInstallationId, getValues, setValue]);

  function connectJira() {
    sessionStorage.setItem("astra_project_create_draft", JSON.stringify(getValues()));
    window.location.href = "/api/integrations/jira/connect?returnTo=%2Fdashboard%2Fprojects";
  }

  function connectGitHub() {
    sessionStorage.setItem("astra_project_create_draft", JSON.stringify(getValues()));
    window.location.href = `/api/integrations/github/connect?returnTo=${encodeURIComponent(githubReturnTo)}`;
  }

  async function loadGitHubRepositories(installationId = getValues("github_installation_id")) {
    setGitHubStatus("loading");
    setGitHubError(null);
    try {
      if (!installationId) throw new Error("Connect GitHub for this project first.");
      const projectId = getValues("project_id");
      if (!projectId) throw new Error("Enter the Astra project ID before loading repositories.");
      const result = await api.githubRepositories(installationId, projectId);
      setGitHubRepositories(result.repositories);
      setGitHubStatus("ready");
    } catch (caught) {
      setGitHubStatus("error");
      setGitHubError(caught instanceof Error ? caught.message : "GitHub repositories could not be loaded.");
    }
  }

  async function loadJiraProjects() {
    setJiraStatus("loading");
    setJiraError(null);
    try {
      const result = await api.jiraProjects();
      setJiraProjects(result.projects);
      setJiraStatus(result.projects.length ? "ready" : "idle");
    } catch (caught) {
      setJiraStatus("error");
      setJiraError(caught instanceof Error ? caught.message : "Jira projects could not be loaded.");
    }
  }

  return (
    <form className="form-panel" onSubmit={handleSubmit(onSubmit)}>
      <label>Project ID<input {...register("project_id")} readOnly={Boolean(defaultValues?.project_id)} /> <FieldError message={errors.project_id?.message} /></label>
      <label>Name<input {...register("name")} /> <FieldError message={errors.name?.message} /></label>
      <label>Description<textarea {...register("description")} /></label>
      <label>Owner employee ID<input {...register("owner_employee_id")} /></label>
      <label>Department<input {...register("department")} /></label>
      <label>Status<select {...register("status")}><option>PLANNED</option><option>ACTIVE</option><option>IN_PROGRESS</option><option>ON_HOLD</option><option>COMPLETED</option><option>ARCHIVED</option></select></label>
      <div className="form-context">
        <span>GitHub</span>
        <button type="button" className="button secondary" onClick={connectGitHub}>Connect GitHub</button>
      </div>
      <input type="hidden" {...register("github_installation_id")} />
      {githubStatus === "error" && <small className="field-error">{githubError}</small>}
      <button type="button" className="button secondary" onClick={() => void loadGitHubRepositories()} disabled={githubStatus === "loading"}>
        {githubStatus === "loading" ? "Loading repositories..." : "Load connected repositories"}
      </button>
      <label>Repository<select {...register("github_repository_id")}><option value="">Select GitHub repository</option>{githubRepositories.map((repository) => <option key={repository.repository_id} value={repository.repository_id}>{repository.full_name || `${repository.organization} / ${repository.repository}`}</option>)}</select></label>
      {showJiraIntegration && <>
        <div className="form-context">
          <span>Jira project (server-selected, no cloud ID entry)</span>
          <button type="button" className="button secondary" onClick={connectJira}>Connect Jira</button>
        </div>
        {jiraStatus === "error" && <small className="field-error">{jiraError ?? "Jira project list could not be loaded."}</small>}
        <button type="button" className="button secondary" onClick={() => void loadJiraProjects()} disabled={jiraStatus === "loading"}>
          {jiraStatus === "loading" ? "Loading projects..." : "Load connected projects"}
        </button>
        <label>Jira project<select {...register("jira_project_id")}><option value="">Select Jira project</option>{jiraProjects.map((project) => <option key={project.id} value={project.id}>{project.key} — {project.name}</option>)}</select></label>
        <label>Project key<input {...register("jira_project_key")} readOnly /></label>
        {jiraStatus === "ready" && <small className="form-hint">The selected Jira project will be saved against the authenticated employee connection.</small>}
      </>}
      <FormActions submitting={submitting} submitLabel={submitLabel} />
    </form>
  );
}

export function TeamForm({ defaultValues, onSubmit, submitting, submitLabel = "Save team" }: FormProps<z.infer<typeof teamSchema>>) {
  const { register, handleSubmit, formState: { errors } } = useForm<z.infer<typeof teamSchema>>({ resolver: zodResolver(teamSchema), defaultValues: { team_id: "", description: "", team_lead_id: "", status: "ACTIVE", ...defaultValues } });
  return <form className="form-panel" onSubmit={handleSubmit(onSubmit)}><label>Team ID<input {...register("team_id")} readOnly={Boolean(defaultValues?.team_id)} /> <FieldError message={errors.team_id?.message} /></label><label>Name<input {...register("name")} /> <FieldError message={errors.name?.message} /></label><label>Description<textarea {...register("description")} /></label><label>Team lead ID<input {...register("team_lead_id")} /></label><label>Status<select {...register("status")}><option>ACTIVE</option><option>INACTIVE</option></select></label><FormActions submitting={submitting} submitLabel={submitLabel} /></form>;
}

export function ProjectMembershipForm({ employees, teams, defaultValues, onSubmit, submitting }: FormProps<z.infer<typeof membershipSchema>> & { employees: Employee[]; teams: Team[] }) {
  const { register, handleSubmit, formState: { errors } } = useForm<z.input<typeof membershipSchema>, unknown, z.output<typeof membershipSchema>>({ resolver: zodResolver(membershipSchema), defaultValues: { employee_id: "", role: "Member", allocation_percentage: 100, team_id: "", status: "ACTIVE", ...defaultValues } });
  const selectedEmployee = employees.find((employee) => employee.employee_id === defaultValues?.employee_id);
  return <form className="form-panel" onSubmit={handleSubmit(onSubmit)}><label>Employee{defaultValues?.employee_id ? <><input value={selectedEmployee?.name ?? defaultValues.employee_id} readOnly /><input type="hidden" {...register("employee_id")} /></> : <select {...register("employee_id")}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.employee_id} value={employee.employee_id}>{employee.name}</option>)}</select>}<FieldError message={errors.employee_id?.message} /></label><label>Role<input {...register("role")} /></label><label>Allocation percentage<input type="number" min="0" max="100" {...register("allocation_percentage")} /><FieldError message={errors.allocation_percentage?.message} /></label><label>Team<select {...register("team_id")}><option value="">No team</option>{teams.map((team) => <option key={team.team_id} value={team.team_id}>{team.name}</option>)}</select></label><label>Status<select {...register("status")}><option>ACTIVE</option><option>INACTIVE</option></select></label><FormActions submitting={submitting} submitLabel="Save membership" /></form>;
}

export function TaskForm({ projectId, employees, teams, defaultValues, onSubmit, submitting, submitLabel = "Save task" }: FormProps<z.infer<typeof taskSchema>> & { projectId: string; employees: Employee[]; teams: Team[] }) {
  const { control, register, handleSubmit, setValue, formState: { errors } } = useForm<z.input<typeof taskSchema>, unknown, z.output<typeof taskSchema>>({ resolver: zodResolver(taskSchema), defaultValues: { task_id: "", title: "", description: "", jira_issue_key: "", assignee_id: "", team_id: "", priority: "MEDIUM", estimated_hours: null, actual_hours: 0, acceptance_criteria: "", ...defaultValues } });
  const selectedTeam = useWatch({ control, name: "team_id" }); const [teamMemberships, setTeamMemberships] = useState<{ teamId: string; employeeIds: string[] } | null>(null); const teamMemberIds = teamMemberships?.teamId === selectedTeam ? teamMemberships.employeeIds : null; const teamMembers = selectedTeam && teamMemberIds ? employees.filter((employee) => teamMemberIds.includes(employee.employee_id)) : selectedTeam ? [] : employees;
  useEffect(() => { if (!selectedTeam) return; let active = true; void api.teamEmployees(projectId, selectedTeam).then((members) => { if (active) setTeamMemberships({ teamId: selectedTeam, employeeIds: members.map((member) => member.employee_id) }); }).catch(() => { if (active) setTeamMemberships({ teamId: selectedTeam, employeeIds: [] }); }); return () => { active = false; }; }, [projectId, selectedTeam]);
  return <form className="form-panel" onSubmit={handleSubmit(onSubmit)}><p className="form-context">Project: {projectId}</p><label>Task ID<input {...register("task_id")} readOnly={Boolean(defaultValues?.task_id)} /> <FieldError message={errors.task_id?.message} /></label><label>Title<input {...register("title")} /> <FieldError message={errors.title?.message} /></label><label>Description<textarea {...register("description")} /></label><label>Jira issue key<input {...register("jira_issue_key")} /></label><label>Team<select {...register("team_id", { onChange: () => setValue("assignee_id", "") })}><option value="">No team</option>{teams.map((team) => <option key={team.team_id} value={team.team_id}>{team.name}</option>)}</select></label>{selectedTeam && <small className="form-hint">Only members of the selected project team can be assigned.</small>}<label>Assignee<select {...register("assignee_id")}><option value="">Unassigned</option>{teamMembers.map((employee) => <option key={employee.employee_id} value={employee.employee_id}>{employee.name}</option>)}</select></label><label>Priority<select {...register("priority")}><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select><FieldError message={errors.priority?.message} /></label><label>Estimated hours<input type="number" min="0" step="0.5" {...register("estimated_hours")} /></label><label>Actual hours<input type="number" min="0" step="0.5" {...register("actual_hours")} /></label><label>Acceptance criteria<textarea placeholder="One criterion per line" {...register("acceptance_criteria")} /></label><FormActions submitting={submitting} submitLabel={submitLabel} /></form>;
}