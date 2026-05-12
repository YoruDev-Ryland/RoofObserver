let timelineChartInstance = null;
function renderTimeline() {
  const ctx = document.getElementById('timelineChart');
  if (!ctx) return;
  
  // Default coordinate (approximate observatory somewhere)
  const lat = 30.6714;
  const lon = -104.0226;
  
  const end = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  
  // 1. Sun & Moon Data Points (every 10 minutes)
  const sunData = [];
  const moonData = [];
  for (let t = start.getTime(); t <= end.getTime(); t += 10 * 60000) {
    const d = new Date(t);
    const sunPos = SunCalc.getPosition(d, lat, lon);
    const moonPos = SunCalc.getMoonPosition(d, lat, lon);
    sunData.push({ x: d, y: sunPos.altitude * 180 / Math.PI });
    moonData.push({ x: d, y: moonPos.altitude * 180 / Math.PI });
  }

  // 2. Building datasets
  // Filter events in the last 24h, sorted ascending time
  const recentEvents = [...state.roofEvents]
    .filter(e => new Date(e.source_ts_utc) >= start)
    .sort((a,b) => new Date(a.source_ts_utc) - new Date(b.source_ts_utc));
    
  // Find all unique buildings in state
  const buildings = Array.from(new Set(state.roofs.map(r => r.building))).sort((a,b) => {
    const numA = parseInt(a.replace(/\D/g, '')) || 0;
    const numB = parseInt(b.replace(/\D/g, '')) || 0;
    return numA - numB;
  });
  
  // For each building, trace it as a stepped line
  const buildingDatasets = buildings.map((bName, i) => {
    // Determine Y pos: evenly spread. Max number at top, min number at bottom.
    // e.g. Y from 10 to 80
    const bNum = parseInt(bName.replace(/\D/g, '')) || (i + 1);
    const yVal = bNum * 3; // Space them out a bit
    
    // Evaluate state at start time by walking all events before start
    const pastEvts = state.roofEvents.filter(e => e.building === bName && new Date(e.source_ts_utc) < start);
    pastEvts.sort((a,b) => new Date(a.source_ts_utc) - new Date(b.source_ts_utc));
    let currentOpen = false;
    if (pastEvts.length) {
      currentOpen = String(pastEvts[pastEvts.length-1].status).toUpperCase() === 'OPEN';
    }
    
    const dataPts = [];
    dataPts.push({ x: start, y: currentOpen ? yVal : null });
    
    // Add points for each transition
    const evts = recentEvents.filter(e => e.building === bName);
    evts.forEach(e => {
      const isO = String(e.status).toUpperCase() === 'OPEN';
      dataPts.push({ x: new Date(e.source_ts_utc), y: currentOpen ? yVal : null }); // pre-step
      dataPts.push({ x: new Date(e.source_ts_utc), y: isO ? yVal : null }); // step
      currentOpen = isO;
    });
    dataPts.push({ x: end, y: currentOpen ? yVal : null });
    
    return {
      label: bName,
      data: dataPts,
      stepped: true,
      borderColor: '#3fb950',
      borderWidth: 2,
      pointRadius: 0,
      spanGaps: false
    };
  });
  
  const datasets = [
    { label: 'Sun Altitude', data: sunData, borderColor: '#f2c94c', pointRadius: 0, yAxisID: 'y' },
    { label: 'Moon Altitude', data: moonData, borderColor: '#cad1d8', pointRadius: 0, borderDash: [5,5], yAxisID: 'y' },
    ...buildingDatasets
  ];

  if (timelineChartInstance) {
    timelineChartInstance.data.datasets = datasets;
    timelineChartInstance.update();
  } else {
    // If running inside Node/JSDOM unexpectedly, avoid creating chart
    if(typeof Chart === 'undefined') return; 
    
    timelineChartInstance = new Chart(ctx, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { labels: { color: '#c9d1d9' } },
          tooltip: {
            callbacks: {
              label: function(context) {
                let val = context.parsed.y;
                if(context.dataset.label.includes('Altitude')) return context.dataset.label + ': ' + val.toFixed(1) + '°';
                return context.dataset.label + ': ' + (val ? 'OPEN' : 'CLOSED');
              }
            }
          }
        },
        scales: {
          x: { type: 'time', time: { unit: 'hour' }, grid: { color: '#30363d' }, ticks: { color: '#8b949e' } },
          y: { grid: { color: '#30363d' }, ticks: { color: '#8b949e' } }
        }
      }
    });
  }
}
