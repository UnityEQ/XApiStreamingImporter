package org.gephi.plugins.xapi.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public class Entities {

    @JsonProperty("mentions")
    public List<Mention> mentions;
}
