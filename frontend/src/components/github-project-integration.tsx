"use client";

import { startTransition, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Project } from "@/lib/types";

type GitHubRepository = {
  repository_id: string;
  organization: string;
  repository: string;
  full_name: string;
};

export function GitHubProjectIntegration({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [selectedRepositoryId, setSelectedRepositoryId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [callbackError, setCallbackError] = useState<string | null>(null);

  useEffect(() => {
    void api.project(projectId)
      .then((value) => {
        setProject(value);
        setSelectedRepositoryId(value.github?.repository_id ?? "");
      })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Project could not be loaded."));
  }, [projectId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("github_status");
    const callbackFailure = params.get("github_error");
    if (!status && !callbackFailure) return;

    const savedRepositoryId = sessionStorage.getItem("astra_github_repository_draft");
    sessionStorage.removeItem("astra_github_repository_draft");
    startTransition(() => {
      if (savedRepositoryId) setSelectedRepositoryId(savedRepositoryId);
      setCallbackError(callbackFailure);
    });
    if (status === "connected") {
      void api.githubRepositories(projectId)
        .then((result) => setRepositories(result.repositories))
        .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "GitHub repositories could not be loaded."))
        .finally(() => setLoading(false));
    }
    params.delete("github_status");
    params.delete("github_error");
    const remainingQuery = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${remainingQuery ? `?${remainingQuery}` : ""}`);
  }, [projectId]);

  async function loadRepositories() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.githubRepositories(projectId);
      setRepositories(result.repositories);
      setSelectedRepositoryId((current) => current || project?.github?.repository_id || "");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "GitHub repositories could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  function connectGitHub() {
    sessionStorage.setItem("astra_github_repository_draft", selectedRepositoryId);
    window.location.href = `/api/integrations/github/connect?returnTo=${encodeURIComponent(`/dashboard/projects/${projectId}`)}`;
  }

  async function saveRepository() {
    if (!selectedRepositoryId) {
      setError("Select a GitHub repository first.");
      return;
    }
    try {
      const updated = await api.updateProject(projectId, {
        github: { provider: "github", repository_id: selectedRepositoryId },
      });
      setProject(updated);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "GitHub repository could not be connected.");
    }
  }

  return (
    <section className="panel">
      <div className="section-heading"><h2>GitHub</h2></div>
      {project?.github && <p>Connected repository: <strong>{project.github.organization} / {project.github.repository}</strong></p>}
      <div className="form-context"><span>GitHub account</span><button type="button" className="button secondary" onClick={connectGitHub}>Connect GitHub</button></div>
      {callbackError && <small className="field-error">GitHub connection failed ({callbackError}). Your project details are still here; try again.</small>}
      {error && <small className="field-error">{error}</small>}
      <button type="button" className="button secondary" onClick={() => void loadRepositories()} disabled={loading}>
        {loading ? "Loading..." : "Load connected repositories"}
      </button>
      <label>Repository<select value={selectedRepositoryId} onChange={(event) => setSelectedRepositoryId(event.target.value)}>
        <option value="">Select GitHub repository</option>
        {repositories.map((repository) => <option key={repository.repository_id} value={repository.repository_id}>{repository.full_name || `${repository.organization} / ${repository.repository}`}</option>)}
      </select></label>
      <div className="button-row">
        <button type="button" className="button primary" onClick={() => void saveRepository()} disabled={!selectedRepositoryId}>Save repository</button>
      </div>
      {project?.github && <small className="form-hint">GitHub pull requests, commits, and issues are available as normalized evidence. Astra project/task state remains unchanged.</small>}
    </section>
  );
}