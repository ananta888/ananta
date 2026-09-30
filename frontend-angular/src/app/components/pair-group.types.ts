/** Hub-backed pair group as returned by `/pair-groups`. */
export interface PairGroup {
  id: string;
  name: string;
  description: string;
  default_permissions: Record<string, boolean>;
  created_at: number;
}

/** Member row of a pair group as returned by `/pair-groups/<id>`. */
export interface PairGroupMember {
  id: string;
  group_id: string;
  user_id: string;
  display_name: string;
}
