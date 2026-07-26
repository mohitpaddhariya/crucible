const rows = [
  { label: "Task completion", values: ["94%", "92%", "78%", "85%"], low: 2 },
  { label: "Intent accuracy", values: ["96%", "93%", "71%", "82%"], low: 2 },
  { label: "Median latency", values: ["1.4s", "1.5s", "2.8s", "2.1s"], low: 2 },
  { label: "Interruptions", values: ["2", "3", "11", "7"], low: 2 },
];

export function VoiceMatrix() {
  return (
    <div className="matrix-wrap">
      <table className="voice-matrix">
        <thead>
          <tr>
            <th>Controlled treatment</th>
            <th>Clean English</th>
            <th>Hinglish</th>
            <th>Punjabi Hindi</th>
            <th>Phone + noise</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <th>{row.label}</th>
              {row.values.map((value, index) => (
                <td className={index === row.low ? "matrix-alert" : ""} key={`${row.label}-${value}`}>
                  {value}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="matrix-note">
        <span>COUNTERFACTUAL DELTA</span>
        <strong>−25 pts</strong>
        <p>Intent accuracy changes when only the speech treatment changes.</p>
      </div>
    </div>
  );
}
