-- Create Clusters Table (Finance, Marketing, etc.)
CREATE TABLE clusters (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT UNIQUE NOT NULL,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create KPIs Table
CREATE TABLE kpis (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cluster_id UUID REFERENCES clusters(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  definition TEXT,
  formula TEXT,
  example_scenario TEXT,
  difficulty_level INT DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create Quiz Results
CREATE TABLE quiz_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  kpi_id UUID REFERENCES kpis(id) ON DELETE CASCADE,
  is_correct BOOLEAN NOT NULL,
  response_time_seconds INT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);