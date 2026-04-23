import { useEffect, useState } from 'react';
import { fetchS3Days, downloadS3Day, deleteDataDay, subscribeDownloadStatus } from '../api';
import './DataDownloadModal.css';

interface S3Day {
  date: string;
  status: 'local' | 'remote' | 'downloading';
  progress?: number;
}

interface Props {
  onClose: () => void;
  onDataChanged: () => void;
}

export default function DataDownloadModal({ onClose, onDataChanged }: Props) {
  const [days, setDays] = useState<S3Day[]>([]);
  const [loading, setLoading] = useState(true);

  const loadDays = async () => {
    try {
      const data = await fetchS3Days();
      setDays(data.days);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDays();
  }, []);

  const handleDownload = async (date: string) => {
    setDays((prev) => prev.map((d) => (d.date === date ? { ...d, status: 'downloading', progress: 0 } : d)));
    await downloadS3Day(date);
    
    // Subscribe to SSE updates
    subscribeDownloadStatus(date, (data) => {
      setDays((prev) =>
        prev.map((d) => {
          if (d.date === date) {
            if (data.status === 'completed') {
              onDataChanged(); // notify parent to refresh local days
              return { ...d, status: 'local' };
            }
            if (data.status === 'error') {
              alert(`Failed to download ${date}: ${data.error}`);
              return { ...d, status: 'remote' };
            }
            return { ...d, status: 'downloading', progress: data.progress };
          }
          return d;
        })
      );
    });
  };

  const handleDelete = async (date: string) => {
    if (!window.confirm(`Are you sure you want to delete data for ${date}?`)) return;
    await deleteDataDay(date);
    setDays((prev) => prev.map((d) => (d.date === date ? { ...d, status: 'remote' } : d)));
    onDataChanged(); // notify parent
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Data Download Manager</h2>
          <button className="close-btn" onClick={onClose}>&times;</button>
        </div>
        
        {loading ? (
          <div>Loading S3 data...</div>
        ) : (
          <div className="data-list">
            {days.map((day) => (
              <div key={day.date} className="data-row">
                <div>
                  <span className="data-date">{day.date}</span>
                </div>
                <div className="data-actions">
                  {day.status === 'local' && (
                    <>
                      <span className="data-status">Installed</span>
                      <button className="btn btn-sm btn-delete" onClick={() => handleDelete(day.date)}>Delete</button>
                    </>
                  )}
                  {day.status === 'downloading' && (
                    <span className="downloading-text">Downloading... {day.progress ? Math.round(day.progress) + '%' : ''}</span>
                  )}
                  {day.status === 'remote' && (
                    <button className="btn btn-sm btn-download" onClick={() => handleDownload(day.date)}>Download</button>
                  )}
                </div>
              </div>
            ))}
            {days.length === 0 && <div>No data found on server.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
