install.packages("Lock5Data")   # solo la prima volta

library(Lock5Data)

data("SleepStudy")

help("SleepStudy")

library(car)

dati <- SleepStudy



mod_completo <- lm(Happiness ~ PoorSleepQuality + DepressionScore + AnxietyScore + StressScore,
                   data = dati)

marginalModelPlots(mod_completo,
                   terms = ~ PoorSleepQuality + DepressionScore + AnxietyScore + StressScore,
                   layout = c(2, 2),
                   ylab = "Happiness",
                   main = "Marginal Model Plots")



avPlot(mod_completo, variable = "PoorSleepQuality")
