/** Allow a Harbor copy command as input while keeping only its image reference. */
export function stripDockerPullPrefix(value: string): string {
  return value.replace(/^\s*docker\s+pull(?:\s+|$)/, "");
}
