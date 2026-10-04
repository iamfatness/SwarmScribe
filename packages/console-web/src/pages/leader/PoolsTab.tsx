import { useState } from "react";
import { api, leaderPath } from "../../api/client";
import type { FollowerOut, FollowerRevoked } from "../../api/types";
import { useAction } from "../../app/useAction";
import { ActionButton } from "../../components/ActionButton";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { ErrorPanel } from "../../components/ErrorPanel";
import { formatCount, formatTime } from "../../lib/format";
import { ActionNotice, ReadState, RefreshButton, shortId, useLeaderRead, type TabProps } from "./common";

function PoolTable({ leader }: TabProps) {
  const status = leader.snapshot?.status;
  if (status === undefined) return <p>No successful poll yet, so pool figures are not known.</p>;
  const names = [
    ...new Set([...status.pools.map((p) => p.pool), ...status.follower_pools.map((p) => p.pool)]),
  ].sort();
  return (
    <>
      <p className="muted">From the poll at {formatTime(leader.snapshot?.taken_at ?? "")}.</p>
      <div className="table-scroll" role="region" aria-label="Pools" tabIndex={0}>
        <table>
          <thead>
            <tr>
              <th scope="col">Pool</th>
              <th scope="col">Queued</th>
              <th scope="col">Leased</th>
              <th scope="col">Active followers</th>
              <th scope="col">Draining</th>
              <th scope="col">Revoked</th>
              <th scope="col">Gone</th>
            </tr>
          </thead>
          <tbody>
            {names.map((name) => {
              const queue = status.pools.find((p) => p.pool === name);
              const followers = status.follower_pools.find((p) => p.pool === name);
              return (
                <tr key={name}>
                  <th scope="row">{name}</th>
                  <td className="num">{formatCount(queue?.queued ?? 0)}</td>
                  <td className="num">{formatCount(queue?.leased ?? 0)}</td>
                  <td className="num">{formatCount(followers?.active ?? 0)}</td>
                  <td className="num">{formatCount(followers?.draining ?? 0)}</td>
                  <td className="num">{formatCount(followers?.revoked ?? 0)}</td>
                  <td className="num">{formatCount(followers?.gone ?? 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PoolsTab({ leader }: TabProps) {
  const read = useLeaderRead<FollowerOut[]>(leader.name, "followers");
  const drain = useAction();
  const [revoking, setRevoking] = useState<FollowerOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const onDrain = async (follower: FollowerOut) => {
    setNotice(null);
    const ok = await drain.run(() => api.post(leaderPath(leader.name, `followers/${follower.id}/drain`)));
    if (ok) {
      setNotice(`Follower ${shortId(follower.id)} is draining.`);
      read.refresh();
    }
  };

  return (
    <>
      <h3>Pools</h3>
      <PoolTable leader={leader} />
      <div className="section-head">
        <h3>Followers</h3>
        <RefreshButton read={read} />
      </div>
      <ActionNotice message={notice} />
      {drain.error !== null && <ErrorPanel error={drain.error} />}
      <ReadState read={read} what="followers">
        {(followers) =>
          followers.length === 0 ? (
            <p>No followers have joined this leader.</p>
          ) : (
            <div className="table-scroll" role="region" aria-label="Followers" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Follower</th>
                    <th scope="col">Pool</th>
                    <th scope="col">State</th>
                    <th scope="col">Device</th>
                    <th scope="col">Leases</th>
                    <th scope="col">Last seen</th>
                    <th scope="col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {followers.map((follower) => (
                    <tr key={follower.id}>
                      <th scope="row">
                        <code>{shortId(follower.id)}</code>
                      </th>
                      <td>{follower.pool}</td>
                      <td>{follower.state}</td>
                      <td>{follower.device ?? "–"}</td>
                      <td className="num">{formatCount(follower.leases)}</td>
                      <td>{formatTime(follower.last_seen_at)}</td>
                      <td className="actions">
                        {follower.state === "active" && (
                          <ActionButton
                            held={leader.role}
                            action="followers.drain"
                            busy={drain.busy}
                            onClick={() => void onDrain(follower)}
                            name={`Drain follower ${shortId(follower.id)}`}
                          >
                            Drain
                          </ActionButton>
                        )}
                        {(follower.state === "active" || follower.state === "draining") && (
                          <ActionButton
                            held={leader.role}
                            action="followers.revoke"
                            danger
                            onClick={() => setRevoking(follower)}
                            name={`Revoke follower ${shortId(follower.id)}`}
                          >
                            Revoke
                          </ActionButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ReadState>
      {revoking !== null && (
        <ConfirmDialog
          title={`Revoke follower ${shortId(revoking.id)}?`}
          message="The follower can no longer take work and its leased jobs go back to the queue. It needs a new join token to come back."
          confirmLabel="Revoke follower"
          onClose={() => setRevoking(null)}
          onConfirm={async () => {
            const answer = await api.post<FollowerRevoked>(
              leaderPath(leader.name, `followers/${revoking.id}/revoke`),
            );
            setNotice(
              `Follower ${shortId(revoking.id)} is revoked; ${answer.released} leased jobs went back to the queue.`,
            );
            read.refresh();
          }}
        />
      )}
    </>
  );
}
