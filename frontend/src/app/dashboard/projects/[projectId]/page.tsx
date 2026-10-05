import { ProjectDetail } from "@/components/projects";
import { GitHubProjectIntegration } from "@/components/github-project-integration";

export default async function ProjectRoute({ params }: { params: Promise<{ projectId: string }> }) {
	const { projectId } = await params;
	return <>
		<ProjectDetail projectId={projectId} />
		<GitHubProjectIntegration projectId={projectId} />
	</>;
}