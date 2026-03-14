import { useState, useEffect } from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'
import { Bar, Line } from 'react-chartjs-2'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
)

interface ScoreBucket {
  bucket: string
  count: number
}

interface ScoreResponse {
  lab_id: string
  buckets: ScoreBucket[]
}

interface TimelineEntry {
  date: string
  count: number
}

interface TimelineResponse {
  lab_id: string
  submissions: TimelineEntry[]
}

interface PassRateEntry {
  task_id: string
  pass_rate: number
  total_submissions: number
}

interface PassRatesResponse {
  lab_id: string
  pass_rates: PassRateEntry[]
}

interface Lab {
  id: string
  name: string
}

const LABS: Lab[] = [
  { id: 'lab-04', name: 'Lab 4' },
  { id: 'lab-05', name: 'Lab 5' },
]

interface FetchState<T> {
  status: 'idle' | 'loading' | 'success' | 'error'
  data: T | null
  error: string | null
}

function Dashboard() {
  const [selectedLab, setSelectedLab] = useState<string>(LABS[0]?.id ?? 'lab-04')
  const [scoresState, setScoresState] = useState<FetchState<ScoreResponse>>({
    status: 'idle',
    data: null,
    error: null,
  })
  const [timelineState, setTimelineState] = useState<FetchState<TimelineResponse>>({
    status: 'idle',
    data: null,
    error: null,
  })
  const [passRatesState, setPassRatesState] = useState<FetchState<PassRatesResponse>>({
    status: 'idle',
    data: null,
    error: null,
  })

  const apiKey = localStorage.getItem('api_key') ?? ''

  useEffect(() => {
    if (!selectedLab || !apiKey) return

    const fetchScores = async () => {
      setScoresState({ status: 'loading', data: null, error: null })
      try {
        const res = await fetch(`/analytics/scores?lab=${selectedLab}`, {
          headers: { Authorization: `Bearer ${apiKey}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: ScoreResponse = await res.json()
        setScoresState({ status: 'success', data, error: null })
      } catch (err) {
        setScoresState({
          status: 'error',
          data: null,
          error: err instanceof Error ? err.message : 'Unknown error',
        })
      }
    }

    const fetchTimeline = async () => {
      setTimelineState({ status: 'loading', data: null, error: null })
      try {
        const res = await fetch(`/analytics/timeline?lab=${selectedLab}`, {
          headers: { Authorization: `Bearer ${apiKey}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: TimelineResponse = await res.json()
        setTimelineState({ status: 'success', data, error: null })
      } catch (err) {
        setTimelineState({
          status: 'error',
          data: null,
          error: err instanceof Error ? err.message : 'Unknown error',
        })
      }
    }

    const fetchPassRates = async () => {
      setPassRatesState({ status: 'loading', data: null, error: null })
      try {
        const res = await fetch(`/analytics/pass-rates?lab=${selectedLab}`, {
          headers: { Authorization: `Bearer ${apiKey}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: PassRatesResponse = await res.json()
        setPassRatesState({ status: 'success', data, error: null })
      } catch (err) {
        setPassRatesState({
          status: 'error',
          data: null,
          error: err instanceof Error ? err.message : 'Unknown error',
        })
      }
    }

    fetchScores()
    fetchTimeline()
    fetchPassRates()
  }, [selectedLab, apiKey])

  const scoresChartData = scoresState.data
    ? {
        labels: scoresState.data.buckets.map((b) => b.bucket),
        datasets: [
          {
            label: 'Score Distribution',
            data: scoresState.data.buckets.map((b) => b.count),
            backgroundColor: 'rgba(54, 162, 235, 0.6)',
            borderColor: 'rgba(54, 162, 235, 1)',
            borderWidth: 1,
          },
        ],
      }
    : { labels: [], datasets: [] }

  const timelineChartData = timelineState.data
    ? {
        labels: timelineState.data.submissions.map((s) => s.date),
        datasets: [
          {
            label: 'Submissions Over Time',
            data: timelineState.data.submissions.map((s) => s.count),
            borderColor: 'rgba(75, 192, 192, 1)',
            backgroundColor: 'rgba(75, 192, 192, 0.2)',
            tension: 0.1,
          },
        ],
      }
    : { labels: [], datasets: [] }

  const chartOptions = {
    responsive: true,
    plugins: {
      legend: {
        position: 'top' as const,
      },
      title: {
        display: true,
      },
    },
  }

  const scoresChartOptions = {
    ...chartOptions,
    plugins: {
      ...chartOptions.plugins,
      title: {
        display: true,
        text: 'Score Distribution',
      },
    },
  }

  const timelineChartOptions = {
    ...chartOptions,
    plugins: {
      ...chartOptions.plugins,
      title: {
        display: true,
        text: 'Submissions Timeline',
      },
    },
  }

  return (
    <div className="dashboard">
      <header className="app-header">
        <h1>Dashboard</h1>
        <select
          value={selectedLab}
          onChange={(e) => setSelectedLab(e.target.value)}
          className="lab-select"
        >
          {LABS.map((lab) => (
            <option key={lab.id} value={lab.id}>
              {lab.name}
            </option>
          ))}
        </select>
      </header>

      <div className="charts-container">
        <div className="chart-card">
          {scoresState.status === 'loading' && <p>Loading scores...</p>}
          {scoresState.status === 'error' && <p>Error: {scoresState.error}</p>}
          {scoresState.status === 'success' && scoresState.data && (
            <Bar data={scoresChartData} options={scoresChartOptions} />
          )}
        </div>

        <div className="chart-card">
          {timelineState.status === 'loading' && <p>Loading timeline...</p>}
          {timelineState.status === 'error' && <p>Error: {timelineState.error}</p>}
          {timelineState.status === 'success' && timelineState.data && (
            <Line data={timelineChartData} options={timelineChartOptions} />
          )}
        </div>
      </div>

      <div className="chart-card pass-rates-card">
        <h2>Pass Rates</h2>
        {passRatesState.status === 'loading' && <p>Loading pass rates...</p>}
        {passRatesState.status === 'error' && <p>Error: {passRatesState.error}</p>}
        {passRatesState.status === 'success' && passRatesState.data && (
          <table className="pass-rates-table">
            <thead>
              <tr>
                <th>Task ID</th>
                <th>Pass Rate</th>
                <th>Total Submissions</th>
              </tr>
            </thead>
            <tbody>
              {passRatesState.data.pass_rates.map((entry) => (
                <tr key={entry.task_id}>
                  <td>{entry.task_id}</td>
                  <td>{(entry.pass_rate * 100).toFixed(1)}%</td>
                  <td>{entry.total_submissions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

export default Dashboard
