import type { ConsentReport } from "../../api/types";
import { formatCount, formatTime } from "../../lib/format";
import { ReadState, RefreshButton, shortId, useLeaderRead, type TabProps } from "./common";

export function ConsentTab({ leader }: TabProps) {
  const read = useLeaderRead<ConsentReport>(leader.name, "consent/report");
  return (
    <>
      <div className="section-head">
        <p className="muted">
          Recordings by consent state, and transcripts made from recordings that are no longer
          consented.
        </p>
        <RefreshButton read={read} />
      </div>
      <ReadState read={read} what="the consent report">
        {(report) => (
          <>
            {report.truncated && (
              <p className="notice">
                The report is cut short; the leader holds more flagged transcripts.
              </p>
            )}
            <h3>By location</h3>
            {report.locations.length === 0 ? (
              <p>This leader has no locations.</p>
            ) : (
              <div
                className="table-scroll"
                role="region"
                aria-label="Consent by location"
                tabIndex={0}
              >
                <table className="medium">
                  <thead>
                    <tr>
                      <th scope="col">Location</th>
                      <th scope="col" className="num">Consented</th>
                      <th scope="col" className="num">Not consented</th>
                      <th scope="col" className="num">Withdrawn</th>
                      <th scope="col" className="num">Missing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.locations.map((row) => (
                      <tr key={row.name}>
                        <th scope="row">{row.name}</th>
                        <td className="num">{formatCount(row.consented)}</td>
                        <td className="num">{formatCount(row.not_consented)}</td>
                        <td className="num">{formatCount(row.withdrawn)}</td>
                        <td className="num">{formatCount(row.missing)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <h3>Transcripts to review</h3>
            {report.flagged.length === 0 ? (
              <p>No transcript was made from a recording that is no longer consented.</p>
            ) : (
              <div
                className="table-scroll"
                role="region"
                aria-label="Transcripts to review"
                tabIndex={0}
              >
                <table className="medium">
                  <thead>
                    <tr>
                      <th scope="col">Job</th>
                      <th scope="col">Recording</th>
                      <th scope="col">Completed</th>
                      <th scope="col">Outputs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.flagged.map((row) => (
                      <tr key={row.job_id}>
                        <th scope="row">
                          <code>{shortId(row.job_id)}</code>
                        </th>
                        <td className="long">
                          {row.location}: <span className="mono">{row.key}</span>
                        </td>
                        <td>{row.completed_at ? formatTime(row.completed_at) : "–"}</td>
                        <td className="long">
                          <ul className="cell-list">
                            {row.outputs.map((output) => (
                              <li key={output} className="mono">
                                {row.output_location}: {output}
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {report.truncated && (
              <p className="notice">
                The report is cut short; the leader holds more flagged transcripts.
              </p>
            )}
          </>
        )}
      </ReadState>
    </>
  );
}
