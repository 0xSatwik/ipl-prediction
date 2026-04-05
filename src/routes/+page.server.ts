import { getHomePayload } from '$lib/server/data';

export async function load() {
	return getHomePayload();
}
